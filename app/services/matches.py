import http
import re
from asyncio import gather
from datetime import datetime
from itertools import chain
from typing import Tuple

import dateutil.parser
import httpx
from bs4 import BeautifulSoup, element
from bs4.element import ResultSet
from fastapi import HTTPException
from redis.asyncio import Redis

from app import constants, schemas, cache
from app.constants import MATCH_URL_WITH_ID, PAST_MATCHES_URL, UPCOMING_MATCHES_URL
from app.core.config import settings
from app.utils import (
    clean_number_string,
    clean_string,
    fix_datetime_tz,
    get_image_url,
    simplify_name,
)


async def match_by_id(id: str, redis_client: Redis) -> schemas.MatchWithDetails:
    """
    Function to fetch a match from VLR, and return the parsed response

    :param id: The match ID
    :param redis_client: A redis instance
    :return: The parsed match
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(MATCH_URL_WITH_ID.format(id), timeout=20.0)
        if response.status_code != http.HTTPStatus.OK:
            raise HTTPException(
                status_code=response.status_code,
                detail="VLR.gg server returned an error",
            )

    soup = BeautifulSoup(response.content, "lxml")

    teams, bans, event, video_data, map_ret, h2h_matches = await gather(
        get_team_data(soup.find_all("div", class_="match-header-vs"), client=redis_client),
        get_ban_data(soup.find_all("div", class_="match-header-note")),
        get_event_data(soup),
        get_video_data(soup.find("div", class_="match-streams-bets-container")),
        get_map_data(soup.find_all("div", class_="vm-stats")),
        get_previous_encounters_data(soup.find("div", class_="wf-card match-h2h")),
    )
    return schemas.MatchWithDetails(
        teams=teams,
        bans=bans,
        event=event,
        videos=video_data,
        data=map_ret[0],
        map_count=map_ret[1],
        previous_encounters=h2h_matches,
    )


async def get_team_data(data: ResultSet, client: Redis) -> list[dict]:
    """
    Function to parse team data
    :param data: The data
    :param client: A redis instance
    :return: The parsed team data
    """
    # Ensure that we have team data to parse
    if len(data) == 0:
        return []

    match_header = data[0]
    names = match_header.find_all("div", class_="wf-title-med")
    images = match_header.find_all("a", class_="match-header-link")
    match_score: list = [
        None,
        None,
    ]  # Default to None for both teams - required for upcoming matches
    if (match_data := match_header.find_all("div", class_="match-header-vs-score")) and (
        match_data := match_data[0].find_all("div", class_="js-spoiler")
    ):
        match_score = (clean_string(match_data[0].get_text())).split(":")

    response = []
    team_mapping = {}
    for i, score in enumerate(match_score):
        name = clean_string(names[i].get_text())
        team_data = {
            "name": name,
            "img": get_image_url(images[i].find("img")["src"]),
            "score": score,
        }
        if team_url := images[i].get("href"):
            team_data["id"] = team_url.split("/")[2]

            if name != "TBD":
                team_mapping[simplify_name(name)] = team_data["id"]
                # `JD Mall JDG Esports\n(JDG Esports)` == `JDG Esports`, apparently
                if "(" in name and ")" in name:
                    team_mapping[simplify_name(re.search(r"\((.*?)\)", name).group(1))] = team_data["id"]
        response.append(team_data)

    if team_mapping and settings.ENABLE_ID_MAP_DB:
        await cache.hset("team", mapping=team_mapping, client=client)
    return response


async def get_ban_data(data: ResultSet) -> list:
    """
    Function to parse the notes from a match page on VLR
    :param data: The notes
    :return: The ban data from the notes
    """
    # The "note" seemed to have map ban information. Will change response key back to note if it has more stuff ever.
    return [ban_data.strip() for ban_data in data[0].get_text().split(";")] if data else []


async def get_event_data(soup: BeautifulSoup) -> dict:
    """
    Function to extract event data from a match page on VLR
    :param soup: The page
    :return: The parsed event data
    """
    event_data = soup.find("div", class_="match-header-super")
    event_link = event_data.find("a", class_="match-header-event")
    event_date: datetime | None = None
    if (
        date_str := " ".join(
            [data.get_text().strip() for data in soup.find_all("div", class_="moment-tz-convert")]
        ).strip()
    ) and constants.TBD not in date_str.lower():
        event_date = fix_datetime_tz(dateutil.parser.parse(date_str, ignoretz=True))

    if soup.find("span", class_="match-header-vs-note mod-upcoming"):
        status = "upcoming"
    elif status_data := soup.find("div", class_="match-header-vs-note"):
        status = clean_string(status_data.get_text()).lower()
    else:
        status = None

    if url := event_link.get("href"):
        match_id = url.split("/")[2]
    else:
        match_id = ""

    ret = {
        "id": match_id,
        "img": get_image_url(event_link.find("img")["src"]),
        "series": event_link.find_all("div")[0].find_all("div")[0].get_text().strip(),
        "stage": clean_string(event_link.find_all("div", class_="match-header-event-series")[0].get_text()),
        "date": event_date,
        "status": status,
    }
    if (patch_data := event_data.find_all("div", class_="wf-tooltip")) and "patch" in (
        patch_data := clean_string(patch_data[-1].get_text())
    ):
        ret["patch"] = clean_string(patch_data.split("\n")[0])
    return ret


async def get_video_data(data: element.Tag) -> dict[str, list]:
    """
    Function to extract information about stream/VOD links from a match page on VLR
    :param data: The data about the videos
    :return: The parsed URLs
    """
    response: dict[str, list] = {
        "streams": [
            {
                "name": name.get_text().strip(),
                "url": url.get("href"),
            }
            for stream in data.find("div", class_="match-streams").find_all("div", class_="wf-card")
            if (name := stream.find("span")) and (url := stream.find("a", class_="match-streams-btn-external"))
        ],
        "vods": [
            {"name": vod.get_text().strip(), "url": vod.get("href")}
            for vod in data.find("div", class_="match-vods").find_all("a", class_="wf-card")
        ],
    }

    response["streams"].extend(
        [
            {
                "name": stream.find("span").get_text().strip(),
                "url": stream.get("href"),
            }
            for stream in data.find_all("a", class_="match-streams-btn")
            if stream.find("span")
        ]
    )

    return response


async def get_map_data(data: ResultSet) -> Tuple[list, int]:
    """
    Function to extract information about a map from a match page on VLR
    :param data: The data about the maps
    :return: The parsed data
    """
    stats = data[0]

    # Extract stats first
    map_stats = stats.find_all("div", class_="vm-stats-game")

    # Extract map names if there were multiple maps
    maps = {
        map_data["data-game-id"]: "".join(i for i in clean_string(map_data.get_text()) if not i.isdigit())
        for map_data in stats.find_all("div", class_="vm-stats-gamesnav-item")
    }

    # If the above dict is empty (i.e. no vm-stats-gamesnav-item), we know that there is a single map
    if maps == {}:
        if map_data := stats.find_all("div", class_="map"):
            maps = {stats["data-game-id"]: map_data[0].find("span").get_text().strip()}
            map_count = 1
        else:
            map_count = 0
    else:
        # Set the number of maps actually played (remove disabled ones basically)
        map_count = len(maps) - 1 - len(stats.find_all("div", class_="mod-disabled"))

    map_ret = []
    for map_data in map_stats:
        if (match_map_id := map_data["data-game-id"]) == "all" or maps.get(match_map_id, "").lower() == constants.TBD:
            continue
        teams = [
            {
                "name": map_data.find_all("div", class_="team-name")[i].get_text().strip(),
                "score": map_data.find_all("div", class_="score")[i].get_text().strip(),
            }
            for i in range(2)
        ]
        team_short_name = [
            clean_string(elem.get_text())
            for elem in map_data.find("div", class_="vlr-rounds").find_all("div", class_="team")
        ]
        team_name_mapping = {short: long["name"] for short, long in zip(team_short_name, teams)}
        rounds = []
        # TODO: find a better solution, only done to prevent warning at 201 (tuple[int, ...] vs tuple[int, int])
        prev: tuple[int, ...] = (0, 0)
        for round_data in map_data.find_all("div", class_="vlr-rounds-row-col")[1:]:
            if round_current_score := round_data.get("title"):
                round_score = clean_string(round_current_score)
                side, round_winner = "", ""
                if round_score != "":
                    current = tuple(map(int, round_score.split("-")))
                    if prev[0] == current[0]:
                        round_winner = "team2"
                    elif prev[1] == current[1]:
                        round_winner = "team1"

                    prev = current

                win_type: str | None = None
                if round_win_data := round_data.find_all("div", class_="mod-win"):
                    side = {
                        "mod-t": "attack",
                        "mod-ct": "defense",
                    }.get(round_win_data[0].get("class")[2], "")

                    if (img := round_win_data[0].find("img")) and (img_src := img.get("src")):
                        win_type = {
                            "elim": "Elimination",
                            "time": "Time out",
                            "defuse": "Defused",
                            "boom": "Spike exploded",
                        }.get(img_src.split("/")[-1].split(".")[0])

                rounds.append(
                    {
                        "round_number": clean_string(round_data.find_all("div", class_="rnd-num")[0].get_text()),
                        "round_score": round_score,
                        "winner": round_winner,
                        "side": side,
                        "win_type": win_type or "Not Played",
                    }
                )

        map_ret.append(
            {
                "map": maps.get(match_map_id),
                "teams": teams,
                "members": list(
                    chain(
                        *(
                            await gather(
                                *[
                                    parse_scoreboard(element, team_name_mapping)
                                    for element in map_data.find_all("tbody")
                                ]
                            )
                        )
                    )
                ),
                "rounds": rounds,
            }
        )
    return map_ret, map_count


async def parse_scoreboard(data: element.Tag, team_name_mapping: dict[str, str]) -> list:
    ret = []
    for player in data.find_all("tr"):
        data = player.find_all("td", class_="mod-player")[0]
        stats = player.find_all("td", class_="mod-stat")
        team_name_short = clean_string(data.find_all("div", class_="ge-text-light")[-1].get_text())
        player_id = ""
        if player_data := data.find("a"):
            player_id = player_data["href"].split("/")[-2]

        ret.append(
            {
                "id": player_id,
                "name": clean_string(data.find_all("div", class_="text-of")[0].get_text()),
                "team": team_name_mapping.get(team_name_short, team_name_short),
                "agents": [
                    {"title": agent["title"], "img": get_image_url(agent["src"])}
                    for agent in player.find_all("td", class_="mod-agents")[0].find_all("img")
                ],
                "rating": clean_number_string(stats[0].find("span", class_="side mod-side mod-both").get_text()),
                "acs": clean_number_string(stats[1].find("span", class_="side mod-side mod-both").get_text()),
                "kills": clean_number_string(stats[2].find("span", class_="side mod-side mod-both").get_text()),
                "deaths": clean_number_string(stats[3].find("span", class_="side mod-both").get_text()),
                "assists": clean_number_string(stats[4].find("span", class_="side mod-both").get_text()),
                "kast": clean_number_string(stats[6].find("span", class_="side mod-both").get_text()),
                "adr": clean_number_string(stats[7].find("span", class_="side mod-both").get_text()),
                "headshot_percent": clean_number_string(stats[8].find("span", class_="side mod-both").get_text()),
                "first_kills": clean_number_string(stats[9].find("span", class_="side mod-both").get_text()),
                "first_deaths": clean_number_string(stats[10].find("span", class_="side mod-both").get_text().strip()),
                "first_kills_diff": clean_number_string(stats[11].find("span", class_="mod-both").get_text().strip()),
            }
        )
    return ret


async def get_previous_encounters_data(data: element.Tag) -> list[dict]:
    """
    :param data: Previous encounters data
    :return: List of match IDs
    """
    response = []
    if data:
        team_a, team_b = [
            team.find("div").get_text().strip() for team in data.find_all("a", class_="match-h2h-header-team")
        ]
        for match_link in data.find_all("a", class_="wf-module-item mod-h2h"):
            match_obj = {
                "match_id": match_link["href"].split("/")[1],
                "teams": [
                    {
                        "name": team_a,
                        "score": match_link.find("span", class_="rf").get_text().strip(),
                    },
                    {
                        "name": team_b,
                        "score": match_link.find("span", class_="ra").get_text().strip(),
                    },
                ],
            }
            response.append(match_obj)
    return response


async def match_list(redis_client: Redis) -> list[schemas.Match]:
    """
    Function to parse a list of matches from the VLR.gg homepage

    :param redis_client: A redis instance
    :return: The parsed matches
    """
    return list(
        chain(
            *(
                await gather(
                    get_upcoming_matches(redis_client),
                    get_completed_matches(redis_client),
                )
            )
        )
    )


async def get_upcoming_matches(redis_client: Redis) -> list[schemas.Match]:
    """
    Function get a list of upcoming matches from VLR

    :param redis_client: A redis instance
    :return: The list of matches
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        upcoming_matches_response = await client.get(UPCOMING_MATCHES_URL)
        if upcoming_matches_response.status_code != http.HTTPStatus.OK:
            raise HTTPException(
                status_code=upcoming_matches_response.status_code,
                detail="VLR.gg server returned an error",
            )

    upcoming_matches = BeautifulSoup(upcoming_matches_response.content, "lxml")

    return await parse_matches(
        upcoming_matches.find_all("div", class_="wf-label"),
        upcoming_matches.find_all("div", class_="wf-card"),
        redis_client,
    )


async def get_completed_matches(redis_client: Redis) -> list[schemas.Match]:
    """
    Function get a list of completed matches from VLR

    :param redis_client: A redis instance
    :return: The list of matches
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        previous_matches_response = await client.get(PAST_MATCHES_URL)
        if previous_matches_response.status_code != http.HTTPStatus.OK:
            raise HTTPException(
                status_code=previous_matches_response.status_code,
                detail="VLR.gg server returned an error",
            )

    previous_matches = BeautifulSoup(previous_matches_response.content, "lxml")

    return await parse_matches(
        previous_matches.find_all("div", class_="wf-label"),
        previous_matches.find_all("div", class_="wf-card"),
        redis_client,
    )


async def parse_matches(dates: ResultSet, match_data: ResultSet, client: Redis) -> list[schemas.Match]:
    """
    Function to parse a list of matches

    :param dates: The dates on which the matches were/will be held
    :param match_data: The matches
    :param client: A redis instance
    :return: The parsed matches
    """

    return list(
        await gather(
            *[
                parse_match(date, match_info, client)
                for date, match_info in [
                    (date, match)
                    for date, matches in zip(dates, match_data[1:])
                    for match in matches.find_all("a", class_="wf-module-item")
                ]
            ]
        )
    )


async def parse_match(date: element.Tag, match_info: element.Tag, client: Redis) -> schemas.Match:
    """
    Function to parse a given match
    :param date: The match's date
    :param match_info: The match to parse
    :return: The parsed match
    """
    match_id = match_info.get("href").split("/")[1]
    team_names = match_info.find_all("div", class_="text-of")
    team_scores = match_info.find_all("div", class_="match-item-vs-team-score")
    status = match_info.find("div", class_="ml-status").get_text().strip().lower()
    date = clean_string(date.get_text().split("\n")[1])
    time = clean_string(match_info.find("div", class_="match-item-time").get_text())
    if time.lower() == constants.TBD:
        date_string = date
    else:
        date_string = date + " " + time

    team1_name = clean_string(team_names[0].get_text())
    team2_name = clean_string(team_names[1].get_text())
    event_name = clean_string(match_info.find("div", class_="match-item-event").get_text().split("\n")[-1])
    team1_id = team2_id = event_id = None
    if settings.ENABLE_ID_MAP_DB:
        team_ids = await cache.hmget(
            "team",
            [simplify_name(team1_name), simplify_name(team2_name)],
            client=client,
        )
        if team_ids and not all(team_ids) and "TBD" not in (team1_name, team2_name):
            match_data = await match_by_id(match_id, client)
            team_ids = [team.id for team in match_data.teams]

        if team_ids:
            team1_id, team2_id = team_ids

        event_id = await cache.hget("event", simplify_name(event_name), client=client)

    return schemas.Match(
        team1=schemas.MatchTeam(
            name=team1_name,
            id=team1_id,
            score=await parse_score(team_scores[0]),
        ),
        team2=schemas.MatchTeam(
            name=team2_name,
            id=team2_id,
            score=await parse_score(team_scores[1]),
        ),
        status=status,
        time=fix_datetime_tz(dateutil.parser.parse(date_string, ignoretz=True)),
        id=match_id,
        event=event_name,
        series=clean_string(match_info.find("div", class_="match-item-event-series").get_text()),
        event_id=event_id,
    )


async def parse_score(data: element.Tag) -> int | None:
    """
    Function that takes in a tag to parse the score
    :param data: The tag
    :return: The score if it exists, else None
    """
    if (score := data.get_text().strip()).isdigit():
        return int(score)
    return None
