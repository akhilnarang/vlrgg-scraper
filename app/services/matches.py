import asyncio
import itertools

import httpx
from bs4 import BeautifulSoup, element
from bs4.element import ResultSet

from app import schemas, utils
from app.constants import MATCH_URL_WITH_ID, PREFIX


async def match_by_id(id: str) -> schemas.MatchWithDetails:
    """
    Function to fetch a match from VLR, and return the parsed response
    :param id: The match ID
    :return: The parsed match
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(MATCH_URL_WITH_ID.format(id))

    soup = BeautifulSoup(response.content, "html.parser")

    teams, bans, event, map_ret, h2h_matches = await asyncio.gather(
        get_team_data(soup.find_all("div", class_="match-header-vs")),
        get_ban_data(soup.find_all("div", class_="match-header-note")),
        get_event_data(soup),
        get_map_data(soup.find_all("div", class_="vm-stats")),
        get_previous_encounters_data(soup.find_all("div", class_="match-h2h-matches")),
    )
    return schemas.MatchWithDetails(teams=teams, bans=bans, event=event, data=map_ret, previous_encounters=h2h_matches)


async def get_team_data(data: ResultSet) -> list[dict]:
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
    # The "note" seemed to have map ban information. Will change response key back to note if it has more stuff ever.
    return [ban_data.strip() for ban_data in data[0].get_text().split(";")] if data else []


async def get_event_data(soup: BeautifulSoup) -> dict:
    event_link = soup.find_all("a", class_="match-header-event")[0]
    return {
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


async def get_map_data(data: ResultSet) -> list:
    stats = data[0]

    maps = {
        map_data["data-game-id"]: "".join(
            i for i in map_data.get_text().strip().replace("\n", "").replace("\t", "") if not i.isdigit()
        )
        for map_data in stats.find_all("div", class_="vm-stats-gamesnav-item")
    }

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


async def parse_scoreboard(data: ResultSet) -> list:
    ret = []
    for team in data.find_all("tr"):
        data = team.find_all("td", class_="mod-player")[0]
        stats = team.find_all("td", class_="mod-stat")
        ret.append(
            {
                "name": data.find_all("div", class_="text-of")[0].get_text().strip(),
                "team": data.find_all("div", class_="ge-text-light")[0].get_text().strip(),
                "agents": [
                    {"title": agent["title"], "img": f"https://www.vlr.gg/{agent['src']}"}
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


async def get_previous_encounters_data(data: ResultSet) -> list[str]:
    """
    :param data: Previous encounters data
    :return: List of match IDs
    """
    return (
        [match_link["href"].split("/")[1] for match_link in data[0].find_all("a", class_="wf-module-item mod-h2h")]
        if data
        else []
    )


async def match_list() -> list[schemas.Match]:
    """
    Function to parse a list of matches from the VLR.gg homepage
    :return: The parsed matches
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(PREFIX)

    soup = BeautifulSoup(response.content, "html.parser")

    return list(
        itertools.chain(
            *(
                await asyncio.gather(
                    parse_matches(soup.find_all("div", class_="js-home-matches-upcoming")[0], "upcoming"),
                    parse_matches(soup.find_all("div", class_="js-home-matches-completed")[0], "completed"),
                )
            )
        )
    )


async def parse_matches(data: element.Tag, match_type: str) -> list[schemas.Match]:
    """
    Function to parse a list of matches
    :param data: The matches
    :param match_type: The type of matches (upcoming or completed)
    :return: The parsed matches
    """
    return list(
        await asyncio.gather(
            *[
                parse_match(match, match_type)
                for match in data.find_all("div", class_="wf-card")[0].find_all("a", class_="mod-match")
            ]
        )
    )


async def parse_match(match: element.Tag, match_type: str) -> schemas.Match:
    """
    Function to parse a given match
    :param match: The match to parse
    :param match_type: The type of match (upcoming or completed)
    :return: The parsed match
    """
    team_names = match.find_all("div", class_="h-match-team-name")
    team_scores = match.find_all("div", class_="h-match-team-score")
    if (status := match.find_all("div", class_="h-match-eta")[0].get_text().strip()) == "LIVE":
        time = None
    else:
        time = status
        status = match_type

    return schemas.Match(
        team1=schemas.MatchTeam(name=team_names[0].get_text().strip(), score=await parse_score(team_scores[0])),
        team2=schemas.MatchTeam(name=team_names[1].get_text().strip(), score=await parse_score(team_scores[1])),
        status=status,
        time=time,
        id=match.get("href").split("/")[0],
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
