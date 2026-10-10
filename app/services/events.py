import asyncio
import http
import itertools
import logging
import re
from datetime import datetime
from typing import NotRequired, TypedDict, cast
from zoneinfo import ZoneInfo

import dateutil.parser
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl
from redis.asyncio import Redis

from app import cache, constants, schemas
from app.core.config import settings
from app.core.connections import get_http_client
from app.exceptions import BadRequestError, ScrapingError
from app.utils import clean_number_string, clean_string, get_class, get_href, get_image_url, simplify_name

logger = logging.getLogger(__name__)


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
        logger.warning(
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
    if settings.ENABLE_ID_MAPPING:
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

    # The overview shows only the active stage, which for a finished event is the
    # playoffs bracket. Group and Swiss tables live on the other stage pages.
    active_stage = soup.select_one("a.wf-subnav-item.mod-active div.wf-subnav-item-title")
    standings = parse_event_standings(
        soup.find("div", class_="event-container"),
        clean_string(active_stage.get_text()) if active_stage else None,
    )
    if stage_pages := parse_stage_pages(soup, id):
        async with get_http_client() as client:
            # Every request settles before the client closes, so one failure cannot strand the others.
            responses = await asyncio.gather(*(client.get(url) for _, url in stage_pages), return_exceptions=True)
        for (stage, url), stage_response in zip(stage_pages, responses, strict=True):
            # A dead stage link loses only that stage's standings, not the whole event.
            if isinstance(stage_response, Exception) or stage_response.status_code != http.HTTPStatus.OK:
                logger.warning("Skipping standings of event stage %s: %r", url, stage_response)
                continue
            stage_soup = BeautifulSoup(stage_response.content, "lxml")
            standings.extend(parse_event_standings(stage_soup.find("div", class_="event-container"), stage))
    event["standings"] = standings

    # Populate cache if enabled and client provided
    if settings.ENABLE_ID_MAPPING and cache_client:
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
    text_values = [value for value in text_values if value]
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


def parse_stage_pages(soup: BeautifulSoup, event_id: str) -> list[tuple[str, str]]:
    """
    Find the event's stage pages that the overview does not show

    :param soup: The parsed event overview page
    :param event_id: The ID of the event
    :return: ``(stage title, URL)`` for each inactive stage
    """
    stages = []
    for link in soup.select("a.wf-subnav-item"):
        href = get_href(link.get("href") or "")
        title = link.find("div", class_="wf-subnav-item-title")
        if "mod-active" not in (link.get("class") or []) and title and href.startswith(f"/event/{event_id}/"):
            stages.append((clean_string(title.get_text()), f"{constants.PREFIX}{href}"))
    return stages


def parse_event_standings(data: Tag | None, stage: str | None = None) -> list[dict[str, str | int]]:
    """
    Parse the group or Swiss tables of one event stage page

    :param data: The page's ``event-container``
    :param stage: The stage title, used as the group name for unnamed tables
    :return: One standing per team row
    """

    def get_team_and_country(columns: list[Tag]) -> tuple[str, str, int | None]:
        """Extract team, country, and the team column index across layout variants."""
        for index, column in enumerate(columns):
            if team_data := column.find("div", class_="event-group-team-name text-of"):
                values = [clean_string(s) for s in team_data.get_text().split("\n")]
                values = [value for value in values if value and value.lower() != "spoiler hidden"]
                if not values:
                    return "", "", index
                if len(values) == 1:
                    return values[0], "", index
                return values[0], values[1], index
        return "", "", None

    def parse_record(value: str) -> tuple[int, int]:
        # VLR uses an en dash in records ("1–0"), but tolerate spacing and
        # separator changes rather than taking down the whole event endpoint.
        numbers = re.findall(r"\d+", value)
        if len(numbers) < 2:
            logger.warning("Could not parse VLR standings record %r; using 0-0", value)
            return 0, 0
        return int(numbers[0]), int(numbers[1])

    def parse_difference(value: str) -> int | float:
        # VLR shows won/lost pairs ("10/2"); older tables showed the signed difference.
        if "/" in value:
            won, lost = parse_record(value)
            return won - lost
        return clean_number_string(value)

    def parse_row(row: Tag, group: str | None = None) -> dict[str, str | int] | None:
        columns = row.find_all("td")
        img_tag = row.find("img")
        if not img_tag or not (img := img_tag.get("src")):
            return None

        team, country, team_column = get_team_and_country(columns)
        if team_column is None:
            return None

        # The logo may occupy its own column. Locate stats relative to the team
        # column instead of relying on the total number of cells. Compact tables
        # combine W-L into one cell; expanded tables expose W, L, and T separately.
        stats = columns[team_column + 1 :]
        if len(stats) == 4:
            wins, losses = parse_record(clean_string(stats[0].get_text()))
            ties = 0
            map_difference = parse_difference(clean_string(stats[1].get_text()))
            round_difference = parse_difference(clean_string(stats[2].get_text()))
            round_delta = clean_number_string(stats[3].get_text())
        elif len(stats) >= 6:
            wins = clean_number_string(stats[0].get_text())
            losses = clean_number_string(stats[1].get_text())
            ties = clean_number_string(stats[2].get_text())
            map_difference = parse_difference(clean_string(stats[3].get_text()))
            round_difference = parse_difference(clean_string(stats[4].get_text()))
            round_delta = clean_number_string(stats[5].get_text())
        else:
            logger.warning("Unexpected VLR standings row with %d stat columns; skipping", len(stats))
            return None

        standing: dict[str, str | int] = {
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
        if group is not None:
            standing["group"] = group
        return standing

    if not data:
        return []

    event_standings = []
    if event_groups := data.find("div", class_="event-groups-container"):
        tables = event_groups.find_all("table", class_="wf-table mod-simple mod-group")
    elif event_table := data.find("table", class_="wf-table mod-simple mod-group"):
        tables = [event_table]
    else:
        return event_standings

    for table in tables:
        title = table.find("th", class_="mod-title")
        group = (clean_string(title.get_text()) if title else "") or stage
        if not (table_body := table.find("tbody")):
            continue
        for row in table_body.find_all("tr"):
            if standing := parse_row(row, group):
                event_standings.append(standing)

    return event_standings


def tier_event_ids(content: bytes) -> list[str]:
    """Parse the event IDs of one tier-filtered events page.

    :param content: HTML of an ``/events/?tier=...`` page.
    :return: Event IDs in listing order.
    """
    soup = BeautifulSoup(content, "lxml")
    ids = []
    for card in soup.find_all("a", class_="wf-card"):
        parts = get_href(card.get("href") or "").strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "event" and parts[1].isdigit():
            ids.append(parts[1])
    return ids


async def tier_event_circuits(stop_ids: set[str] | None = None, max_pages: int = 2) -> dict[str, constants.Circuit]:
    """Map event IDs to circuits from VLR's tier-filtered event listings.

    Active and upcoming events sit on the first pages of every tier tab, so only a
    bounded number of pages is crawled per tier.

    :param stop_ids: Event IDs to resolve, stopping once every one is listed; None reads every listed event.
    :param max_pages: Highest page crawled per tier.
    :return: Event ID mapped to the circuit of the tier that lists it.
    :raises ScrapingError: If any tier page returns a non-200.
    """
    circuits: dict[str, constants.Circuit] = {}
    tiers = dict(constants.TIER_CIRCUITS)
    async with get_http_client() as client:
        page = 1
        while tiers and page <= max_pages:
            responses = await asyncio.gather(
                *(client.get(constants.EVENTS_TIER_URL.format(tier, page)) for tier in tiers)
            )
            exhausted = []
            for (tier, circuit), response in zip(tiers.items(), responses):
                if response.status_code != http.HTTPStatus.OK:
                    raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
                ids = tier_event_ids(response.content)
                if not ids:
                    exhausted.append(tier)
                    continue
                for event_id in ids:
                    circuits.setdefault(event_id, circuit)
            for tier in exhausted:
                tiers.pop(tier)
            if stop_ids is not None and stop_ids <= circuits.keys():
                break
            page += 1
    return circuits
