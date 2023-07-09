import asyncio

import dateutil.parser
import httpx
from bs4 import BeautifulSoup, element

from app import schemas, utils
from app.constants import TEAM_COMPLETED_MATCHES_URL, TEAM_UPCOMING_MATCHES_URL, TEAM_URL


async def get_team_data(id: str) -> schemas.Team:
    """
    Function get a team's data from VLR and return a parsed version
    :param id: The team's ID
    :return: The parsed data
    """

    async with httpx.AsyncClient() as client:
        response, upcoming_matches_response, completed_matches_response = await asyncio.gather(
            *[
                client.get(TEAM_URL.format(id)),
                client.get(TEAM_UPCOMING_MATCHES_URL.format(id)),
                client.get(TEAM_COMPLETED_MATCHES_URL.format(id)),
            ]
        )

    soup = BeautifulSoup(response.content, "lxml")
    upcoming_matches = BeautifulSoup(upcoming_matches_response, "lxml")
    completed_matches = BeautifulSoup(completed_matches_response, "lxml")

    team_info = soup.find("div", class_="team-header")
    name = team_info.find("h1").get_text().strip()
    img = utils.get_image_url(team_info.find("img")["src"])

    tag = ""
    website = None
    twitter = None

    if tag_element := team_info.find("h2"):
        tag = tag_element.get_text().strip()

    for link in soup.find("div", class_="team-header-links").find_all("a"):
        if link := link.get("href"):
            if "twitter.com" in link:
                twitter = link
            else:
                website = link

    country = soup.find("div", class_="team-header-country").get_text().strip()

    team_data = soup.find("div", class_="team-summary-container-1")

    # Rank doesn't show up on VLR sometimes - not sure why. So we default to 0 if we can't find it.
    if rank_div := team_data.find("div", class_="rank-num mod-"):
        rank = rank_div.get_text().strip()
    else:
        rank = 0

    if region_div := team_data.find("div", class_="rating-txt"):
        region = region_div.get_text().strip()
    else:
        region = ""

    roster, upcoming_matches, completed_matches = await asyncio.gather(
        *[
            asyncio.gather(*[parse_player(player) for player in team_data.find_all("div", class_="team-roster-item")]),
            asyncio.gather(
                *[parse_match(match) for match in upcoming_matches.find_all("a", class_="wf-card fc-flex m-item")]
            ),
            asyncio.gather(
                *[parse_match(match) for match in completed_matches.find_all("a", class_="wf-card fc-flex m-item")]
            ),
        ]
    )
    return schemas.Team.model_validate(
        {
            "name": name,
            "tag": tag,
            "img": img,
            "website": website,
            "twitter": twitter,
            "country": country,
            "rank": rank,
            "region": region,
            "roster": roster,
            "upcoming": upcoming_matches,
            "completed": completed_matches,
        }
    )


async def parse_player(player_data: element.Tag) -> dict:
    """
    Function to parse a player's data from VLR
    :param player_data: The HTML data
    :return: The parsed data
    """
    response = {
        "id": player_data.find("a")["href"].split("/")[2],
        "alias": player_data.find("div", class_="team-roster-item-name-alias").get_text().strip(),
        "img": utils.get_image_url(player_data.find("div", class_="team-roster-item-img").find("img")["src"]),
    }
    if name_div := player_data.find("div", class_="team-roster-item-name-real"):
        response["name"] = name_div.get_text().strip()

    if role := player_data.find("div", class_="team-roster-item-name-role"):
        response["role"] = role.get_text().strip()
    elif role := player_data.find("i", class_="fa fa-star"):
        response["role"] = role["title"]
    return response


async def parse_match(match_data: element.Tag) -> dict:
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
    response = {"event": event, "stage": "".join(stage), "id": match_data["href"].split("/")[1]}
    if eta := match_data.find("span", class_="rm-item-score-eta"):
        response["eta"] = eta.get_text().strip()
        response["opponent"] = (
            eta.find_next("div").find_next("div").find_next("div").get_text().strip().split("\n")[0].replace("\t", "")
        )
    elif score := match_data.find("div", class_="m-item-result"):
        response["score"] = score.get_text().strip().replace("\n", "")
        response["opponent"] = (
            score.find_next("div")
            .find_next("div")
            .find_next("div")
            .find_next("div")
            .get_text()
            .strip()
            .split("\n")[0]
            .replace("\t", "")
        )

    response["date"] = utils.fix_datetime_tz(
        dateutil.parser.parse(match_data.find("div", class_="m-item-date").get_text(), ignoretz=True)
    )
    return response
