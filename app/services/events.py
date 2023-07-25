import asyncio
import itertools
from datetime import datetime
from zoneinfo import ZoneInfo

import dateutil.parser
import httpx
from bs4 import BeautifulSoup, element
from fastapi import HTTPException
from starlette import status

from app import constants, schemas, utils
from app.constants import EVENT_URL_WITH_ID, EVENT_URL_WITH_ID_MATCHES, EVENTS_URL, EventStatus, MatchStatus
from app.core.config import settings


async def get_events() -> list[schemas.Event]:
    """
    Fetch a list of events from VLR, and return the parsed response
    :return: Parsed list of events
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(EVENTS_URL)

    soup = BeautifulSoup(response.content, "lxml")
    return list(
        itertools.chain(
            *(
                await asyncio.gather(
                    *[convert_to_list(data) for data in soup.find_all("div", class_="events-container-col")]
                )
            )
        )
    )


async def convert_to_list(events: element.Tag) -> list[schemas.Event]:
    """
    Parse a list of events
    :param events: The events
    :return: The list of parsed events
    """
    return list(await asyncio.gather(*[parse_event(event) for event in events.find_all("a", class_="wf-card")]))


async def parse_event(event: element.Tag) -> schemas.Event:
    """
    Parse an event
    :param event: The HTML
    :return: The event parsed
    """
    event_id = event["href"].split("/")[2]
    title = event.find_all("div", class_="event-item-title")[0].get_text().strip()
    status = event.find_all("span", class_="event-item-desc-item-status")[0].get_text().strip()
    prize = event.find_all("div", class_="mod-prize")[0].get_text().strip().replace("\t", "").split("\n")[0]
    dates = event.find_all("div", class_="mod-dates")[0].get_text().strip().replace("\t", "").split("\n")[0]
    location = (
        event.find_all("div", class_="mod-location")[0]
        .find_all("i", class_="flag")[0]
        .get("class")[1]
        .replace("mod-", "")
    )
    img = utils.get_image_url(event.find_all("div", class_="event-item-thumb")[0].find("img")["src"])
    return schemas.Event(id=event_id, title=title, status=status, prize=prize, dates=dates, location=location, img=img)


async def get_event_by_id(id: str) -> schemas.EventWithDetails:
    """
    Function to fetch an event from VLR, and return the parsed response
    :param id: The event ID
    :return: The parsed event
    """
    events, matches = await asyncio.gather(parse_events_data(id), parse_match_data(id))
    events["matches"] = matches
    return schemas.EventWithDetails(**events)


async def parse_events_data(id: str) -> dict:
    """
    Function to fetch and parse the data for a given event
    :param id: The ID of the event
    :ret: Dict of the parsed data
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(EVENT_URL_WITH_ID.format(id))
    event: dict[str, str | list] = {"id": id}
    soup = BeautifulSoup(response.content, "lxml")

    if (event_header := soup.find_all("div", class_="event-header")) is None:
        raise HTTPException(detail="Event header was missing, please retry", status_code=status.HTTP_400_BAD_REQUEST)

    header = event_header[0]
    event["title"] = header.find_all("h1", class_="wf-title")[0].get_text().strip()
    event["subtitle"] = header.find_all("h2", class_="event-desc-subtitle")[0].get_text().strip()
    event_desc_item_value = header.find_all("div", class_="event-desc-item-value")
    event["dates"] = event_desc_item_value[0].get_text().strip()
    event["prize"] = event_desc_item_value[1].get_text().strip().replace("\t", "").replace("\n", " ")
    event["location"] = event_desc_item_value[2].get_text().strip() or event_desc_item_value[2].find_all(
        "i", class_="flag"
    )[0].get("class")[1].replace("mod-", "")
    event["img"] = utils.get_image_url(header.find_all("div", class_="event-header-thumb")[0].find("img")["src"])

    if prizes_data := soup.find_all("table", class_="wf-table"):
        event["prizes"] = await prizes_parser(prizes_data[-1])

    if teams_container := soup.find_all("div", class_="event-teams-container"):
        event["teams"] = await parse_team_data(teams_container[0])

    match_data = soup.find("div", class_="event-sidebar-matches").find_all("h2", class_="wf-label mod-large")

    match len(match_data):
        case 2:
            event["status"] = EventStatus.ONGOING
        case 1:
            if match_data[0].get_text().strip().split(" ")[0].lower() == "upcoming":
                event["status"] = EventStatus.UPCOMING
            else:
                event["status"] = EventStatus.COMPLETED
        case _:
            event["status"] = EventStatus.UNKNOWN

    event["standings"] = parse_event_standings(soup.find("div", class_="event-container"))
    return event


async def parse_match_data(id: str) -> list:
    async with httpx.AsyncClient() as client:
        response = await client.get(EVENT_URL_WITH_ID_MATCHES.format(id))

    soup = BeautifulSoup(response.content, "lxml")
    return list(
        itertools.chain(
            *(
                await asyncio.gather(
                    *[
                        match_parser(
                            soup.find_all("div", class_="wf-card")[day + 1],
                            date.get_text().strip().replace("\n", "").replace("\t", ""),
                        )
                        for (day, date) in enumerate(soup.find_all("div", class_="wf-label mod-large"))
                    ]
                )
            )
        )
    )


async def prizes_parser(prizes_table: element.Tag) -> list[dict[str, str | dict[str, str]]]:
    """
    Parse prize data
    :param prizes_table: The HTML
    :return: The parsed data as a list
    """
    prizes = []

    for row in prizes_table.find("tbody").find_all("tr")[:3]:
        prize = {}
        row_data = row.find_all("td")
        prize["position"] = row_data[0].get_text().strip()
        prize["prize"] = row_data[1].get_text().strip().replace("\t", "")
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
                "country": team_row.find_all("div", class_="ge-text-light")[0].get_text().strip(),
                "img": utils.get_image_url(team_row.find("img")["src"]),
            }
        prizes.append(prize)
    return prizes


async def match_parser(day_matches: element.Tag, date: str) -> list[dict[str, str | list[str]]]:
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
        if constants.TBD not in time.lower():
            match_timing = (
                dateutil.parser.parse(
                    f"{date.lower().replace('yesterday', '').replace('today', '')} {time}", ignoretz=True
                )
                .replace(tzinfo=ZoneInfo(settings.TIMEZONE))
                .astimezone(ZoneInfo("UTC"))
            )
        match = {
            "id": match_data["href"].split("/")[1],
            "status": match_data.find_all("div", class_="ml-status")[0].get_text().strip().lower(),
        }
        if match_timing:
            match |= {"date": match_timing.date(), "time": match_timing.time().isoformat()}
        else:
            match |= {"date": dateutil.parser.parse(date, ignoretz=True), "time": time}
        team_data = []
        for team in match_data.find_all("div", class_="match-item-vs-team"):
            data = {
                "name": team.find_all("div", class_="match-item-vs-team-name")[0].get_text().strip(),
                "region": team.find_all("span", class_="flag")[0].get("class")[1].replace("mod-", ""),
            }
            score_data = team.find_all("div", class_="match-item-vs-team-score")[0].get_text().strip()
            if score_data.isdigit():
                data["score"] = int(score_data)
            team_data.append(data)
        match["teams"] = team_data
        if match["status"] not in (MatchStatus.LIVE, MatchStatus.TBD):
            match["eta"] = match_data.find_all("div", class_="ml-eta")[0].get_text().strip()

        match_item_event = (
            match_data.find_all("div", class_="match-item-event text-of")[0].get_text().strip().split("\n")
        )
        match["round"] = match_item_event[0].strip()
        match["stage"] = match_item_event[1].strip()
        matches.append(match)
    return matches


async def parse_team_data(team_data: element.Tag) -> list[dict[str, str]]:
    """
    Function to parse team data
    :param team_data: The HTML
    :return: The parsed result as a list
    """
    participants = []
    for team in team_data.find_all("div", class_="wf-card event-team"):
        event_team_name = team.find_all("a", class_="event-team-name")[0]
        name = event_team_name.get_text().strip()
        if name.lower() == constants.TBD:
            continue
        participant = {
            "name": name,
            "id": event_team_name["href"].split("/")[2],
            "img": utils.get_image_url(team.find_all("img", class_="event-team-players-mask-team")[0]["src"]),
        }

        if seed_data := team.find_all("div", class_="wf-module-item"):
            participant["seed"] = seed_data[0].get_text().strip()

        # for player in team.find_all("a", class_="event-team-players-item"):
        #     id = player["href"].split("/")[2]
        #     name = player.get_text().strip()
        #     country = player.find_all("i", class_="flag")[0].get("class")[1].replace("mod-", "")
        #     roster.append({"id": id, "name": name, "country": country})
        # participant["roster"] = roster
        participants.append(participant)
    return participants


def parse_event_standings(data: element.Tag) -> list[dict[str, str | int]]:
    event_standings = []
    if event_groups := data.find("div", class_="event-groups-container"):
        for table in event_groups.find_all("table", class_="wf-table mod-simple mod-group"):
            group = table.find("thead").find("tr").find("th").get_text().strip()
            for row in table.find("tbody").find_all("tr"):
                columns = row.find_all("td")
                img = columns[0].find("img").get("src")
                team, country = (s.strip() for s in columns[0].get_text().split("\n") if s)
                if len(columns) > 5:
                    wins = columns[1].get_text().replace("\t", "").replace("\n", "")
                    losses = columns[2].get_text().replace("\t", "").replace("\n", "")
                    ties = columns[3].get_text().replace("\t", "").replace("\n", "")
                    map_difference = columns[4].get_text().replace("\t", "").replace("\n", "")
                    round_difference = columns[5].get_text().replace("\t", "").replace("\n", "")
                    round_delta = columns[6].get_text().replace("\t", "").replace("\n", "")
                else:
                    wins, losses = columns[1].get_text().replace("\t", "").replace("\n", "").split("–")
                    ties = 0  # TODO: figure out if there's anything for this
                    map_difference = columns[2].get_text().replace("\t", "").replace("\n", "")
                    round_difference = columns[3].get_text().replace("\t", "").replace("\n", "")
                    round_delta = columns[4].get_text().replace("\t", "").replace("\n", "")
                event_standings.append(
                    {
                        "group": group,
                        "logo": utils.get_image_url(img),
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
            img = columns[0].find("img").get("src")
            team, country = (s.strip() for s in columns[0].get_text().split("\n") if s)
            wins = columns[1].get_text().replace("\t", "").replace("\n", "")
            losses = columns[2].get_text().replace("\t", "").replace("\n", "")
            ties = columns[3].get_text().replace("\t", "").replace("\n", "")
            map_difference = columns[4].get_text().replace("\t", "").replace("\n", "")
            round_difference = columns[5].get_text().replace("\t", "").replace("\n", "")
            round_delta = columns[6].get_text().replace("\t", "").replace("\n", "")
            event_standings.append(
                {
                    "logo": utils.get_image_url(img),
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
