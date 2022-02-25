import asyncio
import itertools
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, element
from bs4.element import ResultSet, Tag

from app import schemas, utils
from app.constants import MATCH_URL_WITH_ID, PAST_MATCHES_URL, UPCOMING_MATCHES_URL


async def match_by_id(id: str) -> schemas.MatchWithDetails:
    """
    Function to fetch a match from VLR, and return the parsed response
    :param id: The match ID
    :return: The parsed match
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(MATCH_URL_WITH_ID.format(id))

    soup = BeautifulSoup(response.content, "html.parser")

    teams, bans, event, video_data, map_ret, h2h_matches = await asyncio.gather(
        get_team_data(soup.find_all("div", class_="match-header-vs")),
        get_ban_data(soup.find_all("div", class_="match-header-note")),
        get_event_data(soup),
        get_video_data(soup.find("div", class_="match-streams-bets-container")),
        get_map_data(soup.find_all("div", class_="vm-stats")),
        get_previous_encounters_data(soup.find("div", class_="wf-card match-h2h")),
    )
    return schemas.MatchWithDetails(
        teams=teams, bans=bans, event=event, videos=video_data, data=map_ret, previous_encounters=h2h_matches
    )


async def get_team_data(data: ResultSet) -> list[dict]:
    """
    Function to parse team data
    :param data: The data
    :return: The parsed team data
    """
    match_header = data[0]
    names = match_header.find_all("div", class_="wf-title-med")
    images = match_header.find_all("a", class_="match-header-link")
    if (match_data := match_header.find_all("div", class_="match-header-vs-score")) and (
        match_data := match_data[0].find_all("div", class_="js-spoiler")
    ):
        match_score = (match_data[0].get_text().replace("\n", "").replace("\t", "")).split(":")
    else:
        match_score = [None, None]

    return [
        {
            "name": names[i].get_text().strip(),
            "img": utils.get_image_url(images[i].find("img")["src"]),
            "score": score,
        }
        for i, score in enumerate(match_score)
    ]


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

    ret = {
        "id": event_link["href"].split("/")[2],
        "img": utils.get_image_url(event_link.find("img")["src"]),
        "series": event_link.find_all("div")[0].find_all("div")[0].get_text().strip(),
        "stage": event_link.find_all("div", class_="match-header-event-series")[0]
        .get_text()
        .strip()
        .replace("\t", "")
        .replace("\n", ""),
        "date": soup.find_all("div", class_="match-header-date")[0]
        .get_text()
        .strip()
        .replace("\t", "")
        .replace("\n", " ")
        .replace("    ", ", ")
        .split("   ")[0],
    }
    if (patch_data := event_data.find_all("div")[-1].get_text().strip()) and "patch" in patch_data.lower():
        ret["patch"] = patch_data
    return ret


async def get_video_data(data: ResultSet) -> dict[str, list]:
    """
    Function to extract information about stream/VOD links from a match page on VLR
    :param data: The data about the videos
    :return: The parsed URLs
    """
    response: dict[str, list] = {"streams": [], "vods": []}
    for stream in data.find("div", class_="match-streams").find_all("div", class_="wf-card"):
        if (name := stream.find("span")) and (url := stream.find("a", class_="match-streams-btn-external")):
            response["streams"].append(
                {
                    "name": name.get_text().strip(),
                    "url": url.get("href"),
                }
            )

    for vod in data.find("div", class_="match-vods").find_all("a", class_="wf-card"):
        response["vods"].append({"name": vod.get_text().strip(), "url": vod.get("href")})

    return response


async def get_map_data(data: ResultSet) -> list:
    """
    Function to extract information about a map from a match page on VLR
    :param data: The data about the maps
    :return: The parsed data
    """
    stats = data[0]

    maps = {
        map_data["data-game-id"]: "".join(
            i for i in map_data.get_text().strip().replace("\n", "").replace("\t", "") if not i.isdigit()
        )
        for map_data in stats.find_all("div", class_="vm-stats-gamesnav-item")
    }
    if maps == {}:
        maps = {stats["data-game-id"]: stats.find_all("div", class_="map")[0].find("span").get_text().strip()}
        map_stats = [stats.find_all("div", class_="vm-stats-game")[0]]
    else:
        map_stats = stats.find_all("div", class_="vm-stats-game")
    map_ret = []
    for map_data in map_stats:
        rounds = []
        teams = []
        if (match_map_id := map_data["data-game-id"]) != "all":
            teams = [
                {
                    "name": map_data.find_all("div", class_="team-name")[i].get_text().strip(),
                    "score": map_data.find_all("div", class_="score")[i].get_text().strip(),
                }
                for i in range(2)
            ]
            prev = [0, 0]
            for round_data in map_data.find_all("div", class_="vlr-rounds-row-col")[1:]:
                if round_current_score := round_data.find_all("div", class_="rnd-currscore"):
                    round_score = round_current_score[0].get_text().strip()
                    side, round_winner = "", ""
                    if round_score != "":
                        current = round_score.split("-")
                        if prev[0] == current[0]:
                            round_winner = "team2"
                        elif prev[1] == current[1]:
                            round_winner = "team1"
                        prev = current
                    if round_win_data := round_data.find_all("div", class_="mod-win"):
                        side = {
                            "mod-t": "attack",
                            "mod-ct": "defense",
                        }.get(round_win_data[0].get("class")[2], "Unknown")

                        win_type = {
                            "elim": "Elimination",
                            "time": "Time out",
                            "defuse": "Defused",
                            "boom": "Spike exploded",
                        }.get(round_win_data[0].find("img")["src"].split("/")[-1].split(".")[0], "Not played")
                    else:
                        win_type = "Not Played"
                    rounds.append(
                        {
                            "round_number": round_data.find_all("div", class_="rnd-num")[0].get_text().strip(),
                            "round_score": round_score,
                            "winner": round_winner,
                            "side": side,
                            "win_type": win_type,
                        }
                    )

        map_ret.append(
            {
                "map": maps.get(match_map_id),
                "teams": teams,
                "members": list(
                    itertools.chain(
                        *(await asyncio.gather(*[parse_scoreboard(element) for element in map_data.find_all("tbody")]))
                    )
                ),
                "rounds": rounds,
            }
        )
    return map_ret


async def parse_scoreboard(data: element.Tag) -> list:
    ret = []
    for team in data.find_all("tr"):
        data = team.find_all("td", class_="mod-player")[0]
        stats = team.find_all("td", class_="mod-stat")
        ret.append(
            {
                "name": data.find_all("div", class_="text-of")[0].get_text().strip(),
                "team": data.find_all("div", class_="ge-text-light")[0].get_text().strip(),
                "agents": [
                    {"title": agent["title"], "img": utils.get_image_url(agent["src"])}
                    for agent in team.find_all("td", class_="mod-agents")[0].find_all("img")
                ],
                "acs": stats[0].find_all("span", class_="stats-sq")[0].get_text().strip() or 0,
                "kills": stats[1].find_all("span", class_="stats-sq")[0].get_text().strip() or 0,
                "deaths": stats[2]
                .find_all("span", class_="stats-sq")[0]
                .get_text()
                .strip()
                .replace("/", "")
                .replace("\xa0", "")
                or 0,
                "assists": stats[3].find_all("span", class_="stats-sq")[0].get_text().strip() or 0,
                "adr": stats[6].find_all("span", class_="stats-sq")[0].get_text().strip() or 0,
                "headshot_percent": stats[7].find_all("span", class_="stats-sq")[0].get_text().strip()[:-1] or 0,
            }
        )
    return ret


async def get_previous_encounters_data(data: Tag) -> list[dict]:
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
                    {"name": team_a, "score": match_link.find("span", class_="rf").get_text().strip()},
                    {"name": team_b, "score": match_link.find("span", class_="ra").get_text().strip()},
                ],
            }
            response.append(match_obj)
    return response


async def match_list() -> list[schemas.Match]:
    """
    Function to parse a list of matches from the VLR.gg homepage
    :return: The parsed matches
    """
    async with httpx.AsyncClient() as client:
        upcoming_matches_response = await client.get(UPCOMING_MATCHES_URL)
        previous_matches_response = await client.get(PAST_MATCHES_URL)

    upcoming_matches = BeautifulSoup(upcoming_matches_response.content, "html.parser")
    previous_matches = BeautifulSoup(previous_matches_response.content, "html.parser")

    return list(
        itertools.chain(
            *(
                await asyncio.gather(
                    parse_matches(
                        upcoming_matches.find_all("div", class_="wf-label"),
                        upcoming_matches.find_all("div", class_="wf-card"),
                        "upcoming",
                    ),
                    parse_matches(
                        previous_matches.find_all("div", class_="wf-label"),
                        previous_matches.find_all("div", class_="wf-card"),
                        "completed",
                    ),
                )
            )
        )
    )


async def parse_matches(dates: ResultSet, match_data: ResultSet, match_type: str) -> list[schemas.Match]:
    """
    Function to parse a list of matches
    :param data: The matches
    :param match_type: The type of matches (upcoming or completed)
    :return: The parsed matches
    """
    match_list = []
    for date, matches in zip(dates, match_data[1:]):
        for match in matches.find_all("a", class_="wf-module-item"):
            match_list.append((date, match))
    return list(await asyncio.gather(*[parse_match(date, match_info, match_type) for date, match_info in match_list]))


async def parse_match(date: element.Tag, match_info: element.Tag, match_type: str) -> schemas.Match:
    """
    Function to parse a given match
    :param match: The match to parse
    :param match_type: The type of match (upcoming or completed)
    :return: The parsed match
    """
    team_names = match_info.find_all("div", class_="text-of")
    team_scores = match_info.find_all("div", class_="match-item-vs-team-score")
    status = match_info.find("div", class_="ml-status").get_text().strip()
    return schemas.Match(
        team1=schemas.MatchTeam(name=team_names[0].get_text().strip(), score=await parse_score(team_scores[0])),
        team2=schemas.MatchTeam(name=team_names[1].get_text().strip(), score=await parse_score(team_scores[1])),
        status=status,
        time=datetime.strptime(
            date.get_text().split("\n")[1].strip().replace("\t", "").replace("\n", "")
            + " "
            + match_info.find("div", class_="match-item-time").get_text().strip(),
            "%a, %B %d, %Y %I:%M %p",
        ),
        id=match_info.get("href").split("/")[1],
        event=match_info.find("div", class_="match-item-event").get_text().split("\n")[-1].strip(),
        series=match_info.find("div", class_="match-item-event-series").get_text().strip(),
    )


async def parse_score(data: element.Tag) -> str | None:
    """
    Function that takes in a tag to parse the score
    :param data: The tag
    :return: The score if it exists, else None
    """
    if (score := data.get_text().strip()).isdigit():
        return score
    return None
