import asyncio
import itertools

import httpx
from bs4 import BeautifulSoup, element
from fastapi import HTTPException
from starlette import status

from app import schemas, utils
from app.constants import EVENT_URL_WITH_ID, EVENT_URL_WITH_ID_MATCHES, EVENTS_URL


async def get_events() -> list[schemas.Event]:
    """
    Fetch a list of events from VLR, and return the parsed response
    :return: Parsed list of events
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(EVENTS_URL)

    soup = BeautifulSoup(response.content, "html.parser")
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
    soup = BeautifulSoup(response.content, "html.parser")

    if (event_header := soup.find_all("div", class_="event-header")) is None:
        raise HTTPException(detail="Event header was missing, please retry", status_code=status.HTTP_400_BAD_REQUEST)
    header = event_header[0]
    event["title"] = header.find_all("h1", class_="wf-title")[0].get_text().strip()
    event["subtitle"] = header.find_all("h2", class_="event-desc-subtitle")[0].get_text().strip()
    event_desc_item_value = header.find_all("div", class_="event-desc-item-value")
    event["dates"] = event_desc_item_value[0].get_text().strip()
    event["prize"] = event_desc_item_value[1].get_text().strip().replace("\t", "").replace("\n", " ")
    event["location"] = event_desc_item_value[2].find_all("i", class_="flag")[0].get("class")[1].replace("mod-", "")
    event["img"] = utils.get_image_url(header.find_all("div", class_="event-header-thumb")[0].find("img")["src"])

    if prizes_data := soup.find_all("table", class_="wf-table"):
        event["prizes"] = await prizes_parser(prizes_data[-1])

    if bracket_containers := soup.find_all("div", class_="event-brackets-container"):
        bracket_coroutines = [get_bracket_data(container) for container in bracket_containers]
    else:
        bracket_coroutines = [get_bracket_data(soup)]
    event["brackets"] = list(await asyncio.gather(*bracket_coroutines))

    if teams_container := soup.find_all("div", class_="event-teams-container"):
        event["teams"] = await parse_team_data(teams_container[0])

    return event


async def parse_match_data(id: str) -> list:
    async with httpx.AsyncClient() as client:
        response = await client.get(EVENT_URL_WITH_ID_MATCHES.format(id))

    soup = BeautifulSoup(response.content, "html.parser")
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


async def get_bracket_data(data: BeautifulSoup | element.ResultSet) -> dict:
    """
    Function to get bracket data
    :param data: The HTML
    :return: The parsed data
    """
    if upper_bracket_container := data.find_all("div", class_="bracket-container mod-upper"):
        upper_bracket = await asyncio.gather(
            *[bracket_parser(column) for column in upper_bracket_container[0].find_all("div", class_="bracket-col")]
        )

    elif upper_bracket_container := data.find_all("div", class_="bracket-container mod-upper mod-compact"):
        upper_bracket = await asyncio.gather(
            *[bracket_parser(column) for column in upper_bracket_container[0].find_all("div", class_="bracket-col")]
        )

    else:
        upper_bracket = []

    if lower_bracket_container := data.find_all("div", class_="bracket-container mod-lower"):
        lower_bracket = await asyncio.gather(
            *[bracket_parser(column) for column in lower_bracket_container[0].find_all("div", class_="bracket-col")]
        )
    elif lower_bracket_container := data.find_all("div", class_="bracket-container mod-lower mod-compact"):
        lower_bracket = await asyncio.gather(
            *[bracket_parser(column) for column in lower_bracket_container[0].find_all("div", class_="bracket-col")]
        )
    else:
        lower_bracket = []

    return {"upper": upper_bracket, "lower": lower_bracket}


async def bracket_parser(bracket: element.Tag) -> dict[str, str | list[dict[str, str | list[str]]]]:
    """
    Parse bracket data
    :param bracket: The HTML
    :return: The parsed data as a dict
    """
    matches = []
    title = bracket.find_all("div", class_="bracket-col-label")[0].get_text().strip()
    for match_data in bracket.find_all("a", class_="bracket-item"):
        match = {}
        if href := match_data.get("href"):
            match["id"] = href.split("/")[1]

        if time := match_data.find("div", class_="bracket-item-status"):
            match["time"] = time.get_text().strip()

        teams = []
        for i in range(0, 2):
            data = match_data.find_all("div", class_="bracket-item-team-name")[i]
            teams.append(
                {
                    "name": data.get_text().strip(),
                    "score": match_data.find_all("div", class_="bracket-item-team-score")[i].get_text().strip(),
                    "img": utils.get_image_url(data.find("img")["src"]),
                }
            )
        match["teams"] = teams
        matches.append(match)
    return {"title": title, "matches": matches}


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
        match = {
            "id": match_data["href"].split("/")[1],
            "time": match_data.find_all("div", class_="match-item-time")[0].get_text().strip(),
            "status": match_data.find_all("div", class_="ml-status")[0].get_text().strip().lower(),
            "date": date,
        }
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
        if match["status"] not in ("live", "tbd"):
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
        participant = {
            "name": event_team_name.get_text().strip(),
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
