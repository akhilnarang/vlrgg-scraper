import asyncio
import http
import itertools
from datetime import datetime
from typing import NotRequired, TypedDict, cast
from zoneinfo import ZoneInfo

import dateutil.parser
import httpx
from bs4 import BeautifulSoup, Tag
from app.exceptions import ScrapingError, BadRequestError
from pydantic import HttpUrl
from redis.asyncio import Redis
from sqlalchemy.dialects.sqlite import insert

from app import schemas, cache
import app.constants as constants
from app.core.config import settings
from app.core.connections import vlr_request_semaphore, async_session, get_vlr_client
from app.models import Team, Event, Match, event_teams
from app.utils import clean_number_string, clean_string, get_image_url, normalize_name, simplify_name


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


async def get_events(cache_client: Redis) -> list[schemas.Event]:
    """
    Fetch a list of events from VLR, and return the parsed response

    :param cache_client: A redis client instance
    :return: Parsed list of events
    """
    async with vlr_request_semaphore:
        async with get_vlr_client() as client:
            response = await client.get(constants.EVENTS_URL)
            if response.status_code != http.HTTPStatus.OK:
                raise ScrapingError()

    soup = BeautifulSoup(response.content, "lxml")
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
    event_id = event["href"].split("/")[2]
    title = clean_string(event.find_all("div", class_="event-item-title")[0].get_text())
    status = clean_string(event.find_all("span", class_="event-item-desc-item-status")[0].get_text())
    prize = event.find_all("div", class_="mod-prize")[0].get_text().strip().replace("\t", "").split("\n")[0]
    dates = event.find_all("div", class_="mod-dates")[0].get_text().strip().replace("\t", "").split("\n")[0]
    location = (
        event.find_all("div", class_="mod-location")[0]
        .find_all("i", class_="flag")[0]
        .get("class")[1]
        .replace("mod-", "")
    )
    img = HttpUrl(get_image_url(event.find_all("div", class_="event-item-thumb")[0].find("img")["src"]))
    if settings.ENABLE_ID_MAP_DB:
        await cache.hset("event", {simplify_name(title): event_id}, client)
    return schemas.Event(
        id=event_id,
        title=title,
        status=status,  # type: ignore
        prize=prize,
        dates=dates,
        location=location,
        img=img,
    )


async def get_event_by_id(id: str) -> schemas.EventWithDetails:
    """
    Function to fetch an event from VLR, and return the parsed response
    :param id: The event ID
    :return: The parsed event
    """
    matches = await parse_match_data(id)
    events = await parse_events_data(id, matches)
    events["matches"] = matches

    # Upsert to database
    await upsert_event_data(events, matches, id)

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


async def parse_events_data(id: str, matches: list) -> ParsedEventData:
    """
    Function to fetch and parse the data for a given event
    :param id: The ID of the event
    :ret: Dict of the parsed data
    """

    async def fetch_stage_teams(url: str) -> list[dict[str, str]]:
        async with vlr_request_semaphore:
            async with get_vlr_client() as client:
                response = await client.get(url)
                if response.status_code != http.HTTPStatus.OK:
                    raise ScrapingError()
        soup = BeautifulSoup(response.content, "lxml")
        if teams_container := soup.find_all("div", class_="event-teams-container"):
            return await parse_team_data(teams_container[0])
        return []

    async with vlr_request_semaphore:
        async with get_vlr_client() as client:
            response = await client.get(constants.EVENT_URL_WITH_ID.format(id))
            if response.status_code != http.HTTPStatus.OK:
                raise ScrapingError()

    event: dict[str, str | list] = {"id": id}
    soup = BeautifulSoup(response.content, "lxml")

    if (event_header := soup.find_all("div", class_="event-header")) is None:
        raise BadRequestError(detail="Event header was missing, please retry")

    header = event_header[0]
    event["title"] = clean_string(header.find_all("h1", class_="wf-title")[0].get_text())
    event["subtitle"] = clean_string(header.find_all("h2", class_="event-desc-subtitle")[0].get_text())
    event_desc_item_value = header.find_all("div", class_="event-desc-item-value")
    event["dates"] = clean_string(event_desc_item_value[0].get_text())
    event["prize"] = clean_string(event_desc_item_value[1].get_text())
    event["location"] = clean_string(event_desc_item_value[2].get_text()) or event_desc_item_value[2].find_all(
        "i", class_="flag"
    )[0].get("class")[1].replace("mod-", "")
    event["img"] = get_image_url(header.find_all("div", class_="event-header-thumb")[0].find("img")["src"])

    if prizes_data := soup.find_all("table", class_="wf-table"):
        event["prizes"] = await prizes_parser(prizes_data[-1])

    # Get stages from matches
    matches_stages = {m.get("stage") for m in matches if m.get("stage")}

    # Parse teams from stages that have matches
    stage_urls = []
    if subnav := soup.find("div", class_="wf-subnav"):
        for a in subnav.find_all("a", class_="wf-subnav-item"):
            href = a["href"]
            stage_name = clean_string(a.find("div", class_="wf-subnav-item-title").get_text())
            if stage_name in matches_stages:
                stage_urls.append("https://www.vlr.gg" + href)
    else:
        # No stages, parse from current page
        if teams_container := soup.find_all("div", class_="event-teams-container"):
            event["teams"] = await parse_team_data(teams_container[0])
        else:
            event["teams"] = []
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
        return cast(ParsedEventData, event)

    # Fetch teams from matching stages
    stage_teams = await asyncio.gather(*[fetch_stage_teams(url) for url in stage_urls])

    # Aggregate unique teams
    all_teams = {}
    for teams in stage_teams:
        for team in teams:
            all_teams[team["id"]] = team
    event["teams"] = list(all_teams.values())

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
    return cast(ParsedEventData, event)


async def parse_match_data(id: str) -> list:
    async with vlr_request_semaphore, httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(constants.EVENT_URL_WITH_ID_MATCHES.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError()

    soup = BeautifulSoup(response.content, "lxml")
    return list(
        itertools.chain(
            *(
                await asyncio.gather(
                    *[
                        match_parser(
                            soup.find_all("div", class_="wf-card")[day + 1],
                            clean_string(date.get_text()),
                        )
                        for (day, date) in enumerate(soup.find_all("div", class_="wf-label mod-large"))
                    ]
                )
            )
        )
    )


async def prizes_parser(
    prizes_table: Tag,
) -> list[dict[str, str | dict[str, str]]]:
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
                    team_row.find_all("div", class_="standing-item-team-name")[0]
                    .get_text()
                    .strip()
                    .split("\n")[0]
                    .strip()
                ),
                "id": team_row_anchor[0]["href"].split("/")[2],
                "country": clean_string(team_row.find_all("div", class_="ge-text-light")[0].get_text()),
                "img": get_image_url(team_row.find("img")["src"]),
            }
        prizes.append(prize)
    return prizes


async def match_parser(day_matches: Tag, date: str) -> list[dict[str, str | list[str]]]:
    """
    Parse match data
    :param day_matches: The HTML
    :param date: The match date
    :return: The parsed data as a list
    """
    matches = []
    for match_data in day_matches.find_all("a", class_="match-item"):
        time = match_data.find_all("div", class_="match-item-time")[0].get_text().strip()
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
            "id": match_data["href"].split("/")[1],
            "status": match_data.find_all("div", class_="ml-status")[0].get_text().strip().lower(),
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
                "name": clean_string(team.find_all("div", class_="match-item-vs-team-name")[0].get_text()),
                "region": team.find_all("span", class_="flag")[0].get("class")[1].replace("mod-", ""),
            }
            score_data = clean_string(team.find_all("div", class_="match-item-vs-team-score")[0].get_text())
            if score_data.isdigit():
                data["score"] = int(score_data)
            team_data.append(data)
        match["teams"] = team_data
        if match["status"] not in (constants.MatchStatus.LIVE, constants.MatchStatus.TBD):
            match["eta"] = clean_string(match_data.find_all("div", class_="ml-eta")[0].get_text())

        match_item_event = (
            match_data.find_all("div", class_="match-item-event text-of")[0].get_text().strip().split("\n")
        )
        match["round"] = clean_string(match_item_event[0])
        match["stage"] = clean_string(match_item_event[1])
        matches.append(match)
    return matches


async def parse_team_data(team_data: Tag) -> list[dict[str, str]]:
    """
    Function to parse team data
    :param team_data: The HTML
    :return: The parsed result as a list
    """
    participants = []
    for team in team_data.find_all("div", class_="wf-card event-team"):
        event_team_name = team.find_all("a", class_="event-team-name")[0]
        name = clean_string(event_team_name.get_text())
        if name.lower() == constants.TBD:
            continue
        participant = {
            "name": name,
            "id": event_team_name["href"].split("/")[2],
            "img": get_image_url(team.find_all("img", class_="event-team-players-mask-team")[0]["src"]),
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


def parse_event_standings(data: Tag) -> list[dict[str, str | int]]:
    event_standings = []
    if event_groups := data.find("div", class_="event-groups-container"):
        for table in event_groups.find_all("table", class_="wf-table mod-simple mod-group"):
            group = clean_string(table.find("thead").find("tr").find("th").get_text())
            for row in table.find("tbody").find_all("tr"):
                columns = row.find_all("td")
                img = columns[0].find("img").get("src")
                ties = 0  # TODO: figure out if there's anything for this for the "smaller" tables
                if len(columns) == 6:
                    team_data = columns[1].find("div", class_="event-group-team-name text-of")
                    wins, losses = map(int, clean_string(columns[2].get_text()).split("–"))
                    map_difference = clean_number_string(columns[3].get_text())
                    round_difference = clean_number_string(columns[4].get_text())
                    round_delta = clean_number_string(columns[5].get_text())
                elif len(columns) > 5:
                    team_data = columns[0].find("div", class_="event-group-team-name text-of")
                    wins = clean_number_string(columns[1].get_text())
                    losses = clean_number_string(columns[2].get_text())
                    ties = clean_number_string(columns[3].get_text())
                    map_difference = clean_number_string(columns[4].get_text())
                    round_difference = clean_number_string(columns[5].get_text())
                    round_delta = clean_number_string(columns[6].get_text())
                else:
                    team_data = columns[0].find("div", class_="event-group-team-name text-of")
                    wins, losses = map(int, clean_string(columns[1].get_text()).split("–"))
                    map_difference = clean_number_string(columns[2].get_text())
                    round_difference = clean_number_string(columns[3].get_text())
                    round_delta = clean_number_string(columns[4].get_text())
                team, country = (s.strip() for s in team_data.get_text().split("\n") if s.strip())
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
        for row in event_table.find("tbody").find_all("tr"):
            columns = row.find_all("td")
            if len(columns) < 7:
                img = columns[0].find("img").get("src")
                team, country = (
                    clean_string(s)
                    for s in columns[0].find("div", class_="event-group-team-name text-of").get_text().split("\n")
                    if s
                )
                wins, losses = map(int, clean_string(columns[1].get_text()).split("–"))
                ties = 0  # TODO: figure out if there's anything for this
                map_difference = clean_number_string(columns[2].get_text())
                round_difference = clean_number_string(columns[3].get_text())
                round_delta = clean_number_string(columns[4].get_text())
            else:
                img = columns[0].find("img").get("src")
                team, country = (
                    clean_string(s)
                    for s in columns[0].find("div", class_="event-group-team-name text-of").get_text().split("\n")
                    if s
                )
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


async def upsert_event_data(event_data: dict, matches: list, id: str):
    """Upsert event, teams, and matches into the database.

    Args:
        event_data: Dictionary containing parsed event data.
        matches: List of match data dicts.
        id: The event's unique identifier.
    """
    async with async_session() as session:
        # Upsert teams
        teams = []
        for team_data in event_data.get("teams", []):
            normalized_name = normalize_name(team_data["name"])
            team = Team(
                id=team_data["id"], name=team_data["name"], normalized_name=normalized_name, img=team_data["img"]
            )
            merged_team = await session.merge(team)
            teams.append(merged_team)

        # Upsert event
        event = Event(
            id=id,
            title=event_data["title"],
            status=event_data["status"].value if hasattr(event_data["status"], "value") else str(event_data["status"]),
            prize=event_data["prize"],
            dates=event_data["dates"],
            location=event_data["location"],
            img=event_data["img"],
        )

        await session.merge(event)

        # Bulk insert into event_teams to avoid lazy loading issues
        if teams:
            values = [{"event_id": id, "team_id": team.id} for team in teams]
            stmt = insert(event_teams).values(values).on_conflict_do_nothing()
            await session.execute(stmt)

        # Upsert matches
        for match_data in matches:
            match = Match(
                id=match_data["id"],
                team_a_id=match_data["teams"][0].get("id")
                if len(match_data["teams"]) > 0 and "id" in match_data["teams"][0]
                else None,
                team_b_id=match_data["teams"][1].get("id")
                if len(match_data["teams"]) > 1 and "id" in match_data["teams"][1]
                else None,
                event_id=id,
                status=match_data["status"],
                time=match_data.get("date"),
                series=match_data.get("round", ""),
                event_name=event_data["title"],
            )
            await session.merge(match)

        await session.commit()
