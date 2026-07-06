import asyncio
import http
import itertools
import logging
from datetime import datetime
from typing import NotRequired, TypedDict, cast
from zoneinfo import ZoneInfo

import dateutil.parser
from bs4 import BeautifulSoup, Tag
from app.exceptions import ScrapingError, BadRequestError
from pydantic import HttpUrl
from redis.asyncio import Redis

from app import schemas, cache
import app.constants as constants
from app.core.config import settings
from app.core.connections import get_http_client
from app.utils import clean_number_string, clean_string, get_class, get_href, get_image_url, simplify_name


# VLR serves a fixed number of event cards per page. When fetching "all" pages we request
# them in batches of this size and stop as soon as a page yields no cards.
EVENTS_PAGE_BATCH_SIZE = 5


class ParsedEventData(TypedDict):
    id: str
    title: str
    subtitle: str
    dates: str
    prize: str
    location: str
    status: constants.EventStatus
    img: HttpUrl
    prizes: list
    teams: list
    standings: list
    matches: NotRequired[list]


async def get_events(cache_client: Redis, pages: int = 1) -> list[schemas.Event]:
    """
    Fetch a list of events from VLR, and return the parsed response

    :param cache_client: A redis client instance
    :param pages: How many pages of events to fetch. Defaults to ``1`` (the first page,
        preserving the previous behaviour). A value ``> 1`` fetches pages ``1..pages``
        (page 1 plus the rest concurrently). A value ``<= 0`` fetches ALL pages, requesting
        more until a page returns no events.
    :return: Parsed list of events
    """
    async with get_http_client() as client:
        response = await client.get(constants.EVENTS_URL)
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

        # Page 1 has been fetched above; grab any additional pages while the client is open.
        event_list = await parse_events_page(response.content, cache_client)
        if event_list and pages != 1:
            seen: set[str] = {e.id for e in event_list}
            # Clamp bounded mode; full-history mode cap is enforced inside the helper.
            effective_pages = min(pages, constants.MAX_PAGINATION_PAGES) if pages >= 1 else pages
            event_list.extend(await fetch_additional_events(client, cache_client, effective_pages, seen))

    return event_list


def events_url(page: int) -> str:
    """Build the URL for a given page of the events list."""
    return f"{constants.EVENTS_URL}&page={page}"


async def parse_events_page(content: bytes, cache_client: Redis) -> list[schemas.Event]:
    """Parse all event cards from a single page of HTML."""
    soup = BeautifulSoup(content, "lxml")
    return list(
        itertools.chain(
            *(
                await asyncio.gather(
                    *[
                        convert_to_list(data, cache_client)
                        for data in soup.find_all("div", class_="events-container-col")
                    ]
                )
            )
        )
    )


async def fetch_additional_events(
    client, cache_client: Redis, pages: int, seen: set[str] | None = None
) -> list[schemas.Event]:
    """
    Fetch events beyond page 1, preserving order (page 2, then 3, ...).

    :param client: The shared HTTP client.
    :param cache_client: A redis client instance.
    :param pages: Total pages wanted (already clamped by caller for bounded mode).
        ``> 1`` fetches pages ``2..pages`` concurrently; ``<= 0`` fetches every remaining
        page in batches up to ``MAX_PAGINATION_PAGES`` total, stopping once a page returns
        no events or contributes no new ids. A non-200 on any page raises ScrapingError
        rather than returning a partial list (page 1 is already validated by the caller).
    :param seen: Set of event ids already collected (page 1). New items are filtered against
        this set in all modes; full-history mode also stops when a page adds zero new ids.
    :return: The parsed events from the additional pages, in order.
    """
    event_list: list[schemas.Event] = []
    if seen is None:
        seen = set()

    if pages > 1:
        # Fetch pages 2..N in batches (not one big fan-out) to bound concurrent load on VLR.
        stop = False
        for start in range(2, pages + 1, EVENTS_PAGE_BATCH_SIZE):
            batch = range(start, min(start + EVENTS_PAGE_BATCH_SIZE, pages + 1))
            responses = await asyncio.gather(*(client.get(events_url(p)) for p in batch))
            for response in responses:
                if response.status_code != http.HTTPStatus.OK:
                    raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
                page_events = await parse_events_page(response.content, cache_client)
                if not page_events:
                    stop = True
                    break
                new = [e for e in page_events if e.id not in seen]
                seen.update(e.id for e in new)
                event_list.extend(new)
            if stop:
                break
        return event_list

    # pages <= 0: fetch all remaining pages in batches until empty, zero new ids,
    # or MAX_PAGINATION_PAGES total pages (including the already-fetched page 1) is reached.
    page = 2
    pages_crawled = 1  # page 1 already counted
    while pages_crawled < constants.MAX_PAGINATION_PAGES:
        batch_size = min(EVENTS_PAGE_BATCH_SIZE, constants.MAX_PAGINATION_PAGES - pages_crawled)
        batch = list(range(page, page + batch_size))
        responses = await asyncio.gather(*(client.get(events_url(p)) for p in batch))
        pages_crawled += len(batch)
        stop = False
        for response in responses:
            if response.status_code != http.HTTPStatus.OK:
                raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
            page_events = await parse_events_page(response.content, cache_client)
            if not page_events:
                stop = True
                break
            new = [e for e in page_events if e.id not in seen]
            if not new:
                stop = True
                break
            seen.update(e.id for e in new)
            event_list.extend(new)
        if stop:
            break
        page += batch_size

    return event_list


async def convert_to_list(events: Tag, client: Redis) -> list[schemas.Event]:
    """
    Parse a list of events

    :param client: A redis client instance
     :param events: The events
    :return: The list of parsed events
    """
    return list(await asyncio.gather(*[parse_event(event, client) for event in events.find_all("a", class_="wf-card")]))


async def parse_event(event: Tag, client: Redis) -> schemas.Event:
    """
    Parse an event

    :param client: A redis client instance
    :param event: The HTML
    :return: The event parsed
    """
    event_id = get_href(event["href"]).split("/")[2]
    title = clean_string(event.find("div", class_="event-item-title").get_text())
    raw_status = clean_string(event.find("span", class_="event-item-desc-item-status").get_text()).lower()
    try:
        status = constants.EventStatus(raw_status)
    except ValueError:
        logging.warning(
            "Unknown VLR event status %r for event_id=%s; falling back to UNKNOWN", raw_status, event_id
        )
        status = constants.EventStatus.UNKNOWN
    prize = event.find("div", class_="mod-prize").get_text().strip().replace("\t", "").split("\n")[0]
    dates = event.find("div", class_="mod-dates").get_text().strip().replace("\t", "").split("\n")[0]
    location = get_class(event.find("div", class_="mod-location").find("i", class_="flag").get("class"), 1).replace(
        "mod-", ""
    )
    img = HttpUrl(get_image_url(event.find("div", class_="event-item-thumb").find("img")["src"]))
    parsed_event = schemas.Event(
        id=event_id,
        title=title,
        status=status,
        prize=prize,
        dates=dates,
        location=location,
        img=img,
    )
    if settings.ENABLE_ID_MAP_DB:
        await cache.hset("event", {simplify_name(title): event_id}, client)
    return parsed_event


def get_event_title(header: Tag) -> str:
    """
    Extract the event title from an event-header tag, supporting both the old
    (h1.wf-title) and the redesigned (h1.event-header-main-title) VLR.gg layouts.
    :param header: The div.event-header tag
    :return: The cleaned title
    """
    title_tag = header.find("h1", class_="event-header-main-title") or header.find("h1", class_="wf-title")
    if title_tag is None:
        raise BadRequestError(detail="Event title was missing, please retry")
    return clean_string(title_tag.get_text())


async def get_event_name_and_cache(id: str, client: Redis) -> str:
    """
    Lightweight function to fetch just the event name and populate the cache
    :param id: The event ID
    :param client: Redis client for caching
    :return: The event name
    """
    async with get_http_client() as http_client:
        response = await http_client.get(constants.EVENT_URL_WITH_ID.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

    soup = BeautifulSoup(response.content, "lxml")

    if (event_header := soup.find_all("div", class_="event-header")) is None:
        raise BadRequestError(detail="Event header was missing, please retry")

    header = event_header[0]
    title = get_event_title(header)

    # Populate cache if enabled
    if settings.ENABLE_ID_MAP_DB:
        await cache.hset("event", {simplify_name(title): id}, client)

    return title


async def get_event_by_id(id: str, client: Redis | None = None) -> schemas.EventWithDetails:
    """
    Function to fetch an event from VLR, and return the parsed response
    :param id: The event ID
    :param client: Optional Redis client for caching
    :return: The parsed event
    """
    events, matches = await asyncio.gather(parse_events_data(id, client), parse_match_data(id))
    events["matches"] = matches
    return schemas.EventWithDetails(
        id=events["id"],
        title=events["title"],
        subtitle=events["subtitle"],
        dates=events["dates"],
        prize=events["prize"],
        location=events["location"],
        status=events["status"],
        img=events["img"],
        matches=events["matches"],
        prizes=events.get("prizes", []),
        teams=events.get("teams", []),
        standings=events.get("standings", []),
    )


async def parse_events_data(id: str, cache_client: Redis | None = None) -> ParsedEventData:
    """
    Function to fetch and parse the data for a given event
    :param id: The ID of the event
    :param cache_client: Optional Redis client for caching
    :ret: Dict of the parsed data
    """
    async with get_http_client() as client:
        response = await client.get(constants.EVENT_URL_WITH_ID.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

    event: dict[str, str | list] = {"id": id}
    soup = BeautifulSoup(response.content, "lxml")

    if (event_header := soup.find_all("div", class_="event-header")) is None:
        raise BadRequestError(detail="Event header was missing, please retry")

    header = event_header[0]
    # VLR.gg redesigned the event header: title/subtitle classes changed and the
    # flat event-desc-item-value siblings became label/value pairs under
    # event-header-main-meta. Support both layouts (new first, old fallback).
    event["title"] = get_event_title(header)
    subtitle_tag = header.find("h2", class_="event-header-main-desc") or header.find(
        "h2", class_="event-desc-subtitle"
    )
    event["subtitle"] = clean_string(subtitle_tag.get_text()) if subtitle_tag else ""

    if meta := header.find("div", class_="event-header-main-meta"):
        # New layout: each child div has a div.label naming the field and a div.value
        meta_values: dict[str, Tag] = {}
        for item in meta.find_all("div", recursive=False):
            if (label := item.find("div", class_="label")) and (value := item.find("div", class_="value")):
                meta_values[clean_string(label.get_text()).lower()] = value
        dates_value = meta_values.get("dates")
        prize_value = meta_values.get("prize")
        # The place slot is labelled "Location" for some events and "Region" for others
        location_value = meta_values.get("location") or meta_values.get("region")
        if dates_value is None or prize_value is None or location_value is None:
            raise BadRequestError(detail="Event metadata was missing, please retry")
    else:
        # Old layout: three flat event-desc-item-value siblings (dates, prize, location)
        event_desc_item_value = header.find_all("div", class_="event-desc-item-value")
        if len(event_desc_item_value) < 3:
            raise BadRequestError(detail="Event metadata was missing, please retry")
        dates_value, prize_value, location_value = event_desc_item_value[:3]

    event["dates"] = clean_string(dates_value.get_text())
    event["prize"] = clean_string(prize_value.get_text())
    # Location text may be empty (flag-only); fall back to the flag's country class if present
    location_text = clean_string(location_value.get_text())
    if not location_text and (flag := location_value.find("i", class_="flag")):
        location_text = get_class(flag.get("class"), 1).replace("mod-", "")
    event["location"] = location_text
    event["img"] = get_image_url(header.find("div", class_="event-header-thumb").find("img")["src"])

    event["prizes"] = parse_prizes(soup)

    if teams_container := soup.find_all("div", class_="event-teams-container"):
        event["teams"] = parse_team_data(teams_container[0])

    match_data = soup.find("div", class_="event-sidebar-matches").find_all("h2", class_="wf-label mod-large")

    match len(match_data):
        case 2:
            event["status"] = constants.EventStatus.ONGOING
        case 1:
            if clean_string(match_data[0].get_text()).split(" ")[0].lower() == "upcoming":
                event["status"] = constants.EventStatus.UPCOMING
            else:
                event["status"] = constants.EventStatus.COMPLETED
        case _:
            event["status"] = constants.EventStatus.UNKNOWN

    event["standings"] = parse_event_standings(soup.find("div", class_="event-container"))

    # Populate cache if enabled and client provided
    if settings.ENABLE_ID_MAP_DB and cache_client:
        await cache.hset("event", {simplify_name(event["title"]): id}, cache_client)

    return cast(ParsedEventData, event)


async def parse_match_data(id: str) -> list:
    async with get_http_client() as client:
        response = await client.get(constants.EVENT_URL_WITH_ID_MATCHES.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

    soup = BeautifulSoup(response.content, "lxml")
    return list(
        itertools.chain(
            *(
                match_parser(
                    soup.find_all("div", class_="wf-card")[day + 1],
                    clean_string(date.get_text()),
                )
                for (day, date) in enumerate(soup.find_all("div", class_="wf-label mod-large"))
            )
        )
    )


def parse_prizes(soup: BeautifulSoup) -> list[dict[str, str | dict[str, str]]]:
    label = soup.find(
        class_="wf-label mod-large",
        string=lambda value: value is not None and clean_string(value).lower() == "prize distribution",
    )
    if not label:
        return []

    prize_container = label.find_next_sibling()
    while isinstance(prize_container, Tag) and prize_container.name == "style":
        prize_container = prize_container.find_next_sibling()
    if not isinstance(prize_container, Tag):
        return []

    if prize_grid := prize_container.find("div", class_="wf-ptable"):
        return prizes_grid_parser(prize_grid)

    if prizes_table := (
        prize_container if prize_container.name == "table" and "wf-table" in prize_container.get("class", []) else None
    ) or prize_container.find("table", class_="wf-table"):
        return prizes_parser(prizes_table)

    return []


def prizes_grid_parser(prizes_grid: Tag) -> list[dict[str, str | dict[str, str]]]:
    prizes = []
    rows = prizes_grid.find_all("div", attrs={"role": "row"})
    for row in rows:
        cells = row.find_all("div", attrs={"role": "cell"}, recursive=False)
        if len(cells) < 3:
            continue
        if clean_string(cells[0].get_text()).lower() == "place":
            continue

        prize: dict = {
            "position": clean_string(cells[0].get_text()),
            "prize": clean_string(cells[1].get_text()),
        }
        if team := parse_prize_team(cells[2]):
            prize["team"] = team
        prizes.append(prize)
    return prizes


def parse_prize_team(team_cell: Tag) -> dict[str, str] | None:
    team_anchor = team_cell.find("a")
    if not team_anchor:
        return None

    href = team_anchor.get("href")
    img = team_anchor.find("img")
    if not href or not img or not img.get("src"):
        return None

    country = clean_string(country_tag.get_text()) if (country_tag := team_anchor.find(class_="ge-text-light")) else ""
    text_values = [clean_string(value) for value in team_anchor.stripped_strings]
    text_values = [value for value in text_values if value and value != country]
    if not text_values:
        return None

    return {
        "name": text_values[0],
        "id": get_href(href).split("/")[2],
        "country": country,
        "img": get_image_url(img["src"]),
    }


def prizes_parser(prizes_table: Tag) -> list[dict[str, str | dict[str, str]]]:
    """
    Parse prize data
    :param prizes_table: The HTML
    :return: The parsed data as a list
    """
    prizes = []

    for row in prizes_table.find("tbody").find_all("tr")[:3]:
        prize: dict = {}
        row_data = row.find_all("td")
        prize["position"] = clean_string(row_data[0].get_text())
        prize["prize"] = clean_string(row_data[1].get_text())
        team_row = row_data[2]
        if team_row_anchor := team_row.find_all("a"):
            prize["team"] = {
                "name": (
                    team_row.find("div", class_="standing-item-team-name").get_text().strip().split("\n")[0].strip()
                ),
                "id": team_row_anchor[0]["href"].split("/")[2],
                "country": clean_string(team_row.find("div", class_="ge-text-light").get_text()),
                "img": get_image_url(team_row.find("img")["src"]),
            }
        prizes.append(prize)
    return prizes


def match_parser(day_matches: Tag, date: str) -> list[dict[str, str | list[str]]]:
    """
    Parse match data
    :param day_matches: The HTML
    :param date: The match date
    :return: The parsed data as a list
    """
    matches = []
    for match_data in day_matches.find_all("a", class_="match-item"):
        time = match_data.find("div", class_="match-item-time").get_text().strip()
        match_timing: datetime | None = None
        date = date.lower().replace("yesterday", "").replace("today", "")
        if constants.TBD not in time.lower():
            match_timing = (
                dateutil.parser.parse(
                    f"{date} {time}",
                    ignoretz=True,
                )
                .replace(tzinfo=ZoneInfo(settings.TIMEZONE))
                .astimezone(ZoneInfo("UTC"))
            )
        match = {
            "id": get_href(match_data["href"]).split("/")[1],
            "status": match_data.find("div", class_="ml-status").get_text().strip().lower(),
        }
        if match_timing:
            match |= {
                "date": match_timing.date(),
                "time": match_timing.time().isoformat(),
            }
        else:
            match |= {"date": dateutil.parser.parse(date, ignoretz=True), "time": time}
        team_data = []
        for team in match_data.find_all("div", class_="match-item-vs-team"):
            data = {
                "name": clean_string(team.find("div", class_="match-item-vs-team-name").get_text()),
                "region": get_class(team.find("span", class_="flag").get("class"), 1).replace("mod-", ""),
            }
            score_data = clean_string(team.find("div", class_="match-item-vs-team-score").get_text())
            if score_data.isdigit():
                data["score"] = int(score_data)
            team_data.append(data)
        match["teams"] = team_data
        if match["status"] not in (constants.MatchStatus.LIVE, constants.MatchStatus.TBD):
            match["eta"] = clean_string(match_data.find("div", class_="ml-eta").get_text())

        match_item_event = match_data.find("div", class_="match-item-event text-of").get_text().strip().split("\n")
        match["round"] = clean_string(match_item_event[0])
        match["stage"] = clean_string(match_item_event[1])
        matches.append(match)
    return matches


def parse_team_data(team_data: Tag) -> list[dict[str, str]]:
    """
    Function to parse team data
    :param team_data: The HTML
    :return: The parsed result as a list
    """
    participants = []
    for team in team_data.find_all("div", class_="wf-card event-team"):
        event_team_name = team.find("a", class_="event-team-name")
        name = clean_string(event_team_name.get_text())
        if name.lower() == constants.TBD:
            continue
        participant = {
            "name": name,
            "id": get_href(event_team_name["href"]).split("/")[2],
            "img": get_image_url(team.find("img", class_="event-team-players-mask-team")["src"]),
        }

        if seed_data := team.find_all("div", class_="wf-module-item"):
            participant["seed"] = clean_string(seed_data[0].get_text())

        # for player in team.find_all("a", class_="event-team-players-item"):
        #     id = player["href"].split("/")[2]
        #     name = player.get_text().strip()
        #     country = player.find_all("i", class_="flag")[0].get("class")[1].replace("mod-", "")
        #     roster.append({"id": id, "name": name, "country": country})
        # participant["roster"] = roster
        participants.append(participant)
    return participants


def parse_event_standings(data: Tag | None) -> list[dict[str, str | int]]:
    def get_team_and_country(columns: list[Tag]) -> tuple[str, str]:
        """Extract team and country from a standings row across layout variants."""
        for column in columns:
            if team_data := column.find("div", class_="event-group-team-name text-of"):
                values = [clean_string(s) for s in team_data.get_text().split("\n")]
                values = [value for value in values if value and value.lower() != "spoiler hidden"]
                if not values:
                    return "", ""
                if len(values) == 1:
                    return values[0], ""
                return values[0], values[1]
        return "", ""

    if not data:
        return []

    event_standings = []
    if event_groups := data.find("div", class_="event-groups-container"):
        for table in event_groups.find_all("table", class_="wf-table mod-simple mod-group"):
            group_header = table.find("thead")
            group = clean_string(group_header.get_text()) if group_header else ""
            table_body = table.find("tbody")
            if not table_body:
                continue
            for row in table_body.find_all("tr"):
                columns = row.find_all("td")
                img_tag = row.find("img")
                if not img_tag:
                    continue
                img = img_tag.get("src")
                if not img:
                    continue
                team, country = get_team_and_country(columns)
                ties = 0  # TODO: figure out if there's anything for this for the "smaller" tables
                if len(columns) == 6:
                    wins, losses = map(int, clean_string(columns[2].get_text()).split("–"))
                    map_difference = clean_number_string(columns[3].get_text())
                    round_difference = clean_number_string(columns[4].get_text())
                    round_delta = clean_number_string(columns[5].get_text())
                elif len(columns) > 5:
                    wins = clean_number_string(columns[1].get_text())
                    losses = clean_number_string(columns[2].get_text())
                    ties = clean_number_string(columns[3].get_text())
                    map_difference = clean_number_string(columns[4].get_text())
                    round_difference = clean_number_string(columns[5].get_text())
                    round_delta = clean_number_string(columns[6].get_text())
                else:
                    wins, losses = map(int, clean_string(columns[1].get_text()).split("–"))
                    map_difference = clean_number_string(columns[2].get_text())
                    round_difference = clean_number_string(columns[3].get_text())
                    round_delta = clean_number_string(columns[4].get_text())
                event_standings.append(
                    {
                        "group": group,
                        "logo": get_image_url(img),
                        "team": team,
                        "country": country,
                        "wins": wins,
                        "losses": losses,
                        "ties": ties,
                        "map_difference": map_difference,
                        "round_difference": round_difference,
                        "round_delta": round_delta,
                    }
                )
    elif event_table := data.find("table", class_="wf-table mod-simple mod-group"):
        table_body = event_table.find("tbody")
        if not table_body:
            return event_standings
        for row in table_body.find_all("tr"):
            columns = row.find_all("td")
            img_tag = row.find("img")
            if not img_tag:
                continue
            img = img_tag.get("src")
            if not img:
                continue
            team, country = get_team_and_country(columns)
            if len(columns) < 7:
                wins, losses = map(int, clean_string(columns[1].get_text()).split("–"))
                ties = 0  # TODO: figure out if there's anything for this
                map_difference = clean_number_string(columns[2].get_text())
                round_difference = clean_number_string(columns[3].get_text())
                round_delta = clean_number_string(columns[4].get_text())
            else:
                wins = clean_number_string(columns[1].get_text())
                losses = clean_number_string(columns[2].get_text())
                ties = clean_number_string(columns[3].get_text())
                map_difference = clean_number_string(columns[4].get_text())
                round_difference = clean_number_string(columns[5].get_text())
                round_delta = clean_number_string(columns[6].get_text())
            event_standings.append(
                {
                    "logo": get_image_url(img),
                    "team": team,
                    "country": country,
                    "wins": wins,
                    "losses": losses,
                    "ties": ties,
                    "map_difference": map_difference,
                    "round_difference": round_difference,
                    "round_delta": round_delta,
                }
            )
    return event_standings
