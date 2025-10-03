import asyncio
import http

import dateutil.parser
import httpx
from bs4 import BeautifulSoup, Tag
from app.exceptions import ScrapingError

from app import schemas, utils
import app.constants as constants
from app.core.connections import vlr_request_semaphore, async_session
from app.models import Player, Team
from app.utils import normalize_name


async def get_team_data(id: str) -> schemas.Team:
    """
    Function get a team's data from VLR and return a parsed version
    :param id: The team's ID
    :return: The parsed data
    """

    async with httpx.AsyncClient(timeout=constants.REQUEST_TIMEOUT) as client:
        async with vlr_request_semaphore:
            response = await client.get(constants.TEAM_URL.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError()

        async with vlr_request_semaphore:
            upcoming_matches_response = await client.get(constants.TEAM_UPCOMING_MATCHES_URL.format(id))
        if upcoming_matches_response.status_code != http.HTTPStatus.OK:
            raise ScrapingError()

        async with vlr_request_semaphore:
            completed_matches_response = await client.get(constants.TEAM_COMPLETED_MATCHES_URL.format(id))

        if completed_matches_response.status_code != http.HTTPStatus.OK:
            raise ScrapingError()

    soup = BeautifulSoup(response.content, "lxml")
    upcoming_matches = BeautifulSoup(upcoming_matches_response.content, "lxml")
    completed_matches = BeautifulSoup(completed_matches_response.content, "lxml")

    team_info = soup.find("div", class_="team-header")
    name = utils.clean_string(team_info.find("h1").get_text())
    img = utils.get_image_url(team_info.find("img")["src"])

    tag = ""
    website = None
    twitter = None

    if tag_element := team_info.find("h2"):
        tag = utils.clean_string(tag_element.get_text())

    for link in soup.find("div", class_="team-header-links").find_all("a"):
        if link := link.get("href"):
            link = utils.expand_url(link)
            if link and "twitter.com" in link:
                twitter = link
            else:
                website = link

    country = utils.clean_string(soup.find("div", class_="team-header-country").get_text())

    team_data = soup.find("div", class_="team-summary-container-1")

    # Rank doesn't show up on VLR sometimes - not sure why. So we default to 0 if we can't find it.
    if rank_div := team_data.find("div", class_="rank-num mod-"):
        rank = utils.clean_number_string(rank_div.get_text())
    else:
        rank = 0

    if region_div := team_data.find("div", class_="rating-txt"):
        region = utils.clean_string(region_div.get_text())
    else:
        region = ""

    results = await asyncio.gather(
        asyncio.gather(*[parse_player(player) for player in team_data.find_all("div", class_="team-roster-item")]),
        asyncio.gather(
            *[parse_match(match) for match in upcoming_matches.find_all("a", class_="wf-card fc-flex m-item")]
        ),
        asyncio.gather(
            *[parse_match(match) for match in completed_matches.find_all("a", class_="wf-card fc-flex m-item")]
        ),
    )
    roster, upcoming_match_list, completed_match_list = results

    team_dict = {
        "name": name,
        "tag": tag,
        "img": img,
        "website": website,
        "twitter": twitter,
        "country": country,
        "rank": rank,
        "region": region,
        "roster": roster,
        "upcoming": upcoming_match_list,
        "completed": completed_match_list,
    }

    # Upsert to database
    await upsert_team_data(team_dict, id)

    return schemas.Team.model_validate(team_dict)


async def parse_player(player_data: Tag) -> dict:
    """
    Function to parse a player's data from VLR
    :param player_data: The HTML data
    :return: The parsed data
    """
    response = {
        "id": player_data.find("a")["href"].split("/")[2],
        "alias": utils.clean_string(player_data.find("div", class_="team-roster-item-name-alias").get_text()),
        "img": utils.get_image_url(player_data.find("div", class_="team-roster-item-img").find("img")["src"]),
    }
    if name_div := player_data.find("div", class_="team-roster-item-name-real"):
        response["name"] = utils.clean_string(name_div.get_text())

    if role := player_data.find("div", class_="team-roster-item-name-role"):
        response["role"] = utils.clean_string(role.get_text())
    elif role := player_data.find("i", class_="fa fa-star"):
        response["role"] = utils.clean_string(role["title"])
    return response


async def parse_match(match_data: Tag) -> dict:
    """
    Function to parse a match's data from VLR
    :param match_data: The HTML data
    :return: The parsed data
    """
    event, *stage = [
        f
        for f in match_data.find("div", class_="m-item-event text-of").get_text().strip().replace("\t", "").split("\n")
        if f
    ]
    response = {
        "event": event,
        "stage": "".join(stage),
        "id": match_data["href"].split("/")[1],
    }
    if eta := match_data.find("span", class_="rm-item-score-eta"):
        response["eta"] = utils.clean_string(eta.get_text())
        response["opponent"] = utils.clean_string(
            eta.find_next("div").find_next("div").find_next("div").get_text().strip().split("\n")[0]
        )
    elif score := match_data.find("div", class_="m-item-result"):
        response["score"] = utils.clean_string(score.get_text())
        opponent_div = score.find_next("div").find_next("div").find_next("div")
        if "ff" in response["score"].lower():
            response["opponent"] = utils.clean_string(opponent_div.get_text().strip().split("\n")[0])
        else:
            response["opponent"] = utils.clean_string(opponent_div.find_next("div").get_text().strip().split("\n")[0])

    response["date"] = utils.fix_datetime_tz(
        dateutil.parser.parse(match_data.find("div", class_="m-item-date").get_text(), ignoretz=True)
    )
    return response


async def upsert_team_data(team_data: dict, id: str):
    """Upsert team and its roster players into the database.

    Args:
        team_data: Dictionary containing parsed team data from VLR.
        id: The team's unique identifier.
    """
    async with async_session() as session:
        normalized_name = normalize_name(team_data["name"])
        team = Team(
            id=id,
            name=team_data["name"],
            normalized_name=normalized_name,
            tag=team_data["tag"],
            img=team_data["img"],
            website=team_data["website"],
            twitter=team_data["twitter"],
            country=team_data["country"],
            rank=team_data["rank"],
            region=team_data["region"],
        )
        await session.merge(team)

        # Upsert players
        for player_data in team_data["roster"]:
            role = player_data.get("role")
            player_type = "player"
            if role:
                role_lower = role.lower()
                if "coach" in role_lower:
                    player_type = "coach"
                elif "manager" in role_lower:
                    player_type = "manager"
                elif "captain" in role_lower or "igl" in role_lower:
                    player_type = "igl"
            player = Player(
                id=player_data["id"],
                name=player_data.get("name"),
                alias=player_data["alias"],
                role=role,
                player_type=player_type,
                img=player_data["img"],
                team_id=id,
            )
            await session.merge(player)

        await session.commit()
