import asyncio

import httpx
from bs4 import BeautifulSoup, element

from app import utils
from app.constants import PLAYER_URL


async def get_player_data(id: str) -> dict:
    """
    Function get a player's data from VLR and return a parsed version
    :param id: The player's ID
    :return: The parsed data
    """

    async with httpx.AsyncClient() as client:
        response = await client.get(PLAYER_URL.format(id))

    soup = BeautifulSoup(response.content, "lxml")
    player_info = soup.find("div", class_="player-header")
    player_data = {
        "alias": player_info.find("h1").get_text().strip(),
        "name": player_info.find("h2").get_text().strip(),
        "img": utils.get_image_url(player_info.find("img")["src"]),
        "country": player_info.find("div", class_="ge-text-light").get_text().strip(),
        "agents": await asyncio.gather(
            *[parse_agent_data(agent.find_all("td")) for agent in soup.find("tbody").find_all("tr")]
        ),
    }

    for link in player_info.find_all("a"):
        if "twitter.com" in link["href"]:
            player_data["twitter"] = link.get_text().strip()
        elif "twitch.tv" in link["href"]:
            player_data["twitch"] = link["href"]
    return player_data


async def parse_agent_data(agent_data: element.ResultSet) -> dict:
    """
    Function to parse agent data from a player's page on VLR
    :param agent_data: An agent table row
    :return: The parsed data
    """
    img = agent_data[0].find("img")
    count, percent = agent_data[1].get_text().strip().split(" ")
    response = {
        "name": img["alt"],
        "img": utils.get_image_url(img["src"]),
        "count": count.replace("(", "").replace(")", ""),
        "percent": percent[:-1],
        "rounds": agent_data[2].get_text().strip(),
        "rating": agent_data[3].get_text().strip() or 0,
        "acs": agent_data[4].get_text().strip(),
        "kd": agent_data[5].get_text().strip(),
        "adr": agent_data[6].get_text().strip() or 0,
        "kast": agent_data[7].get_text().strip()[:-1] or 0,
        "kpr": agent_data[8].get_text().strip(),
        "apr": agent_data[9].get_text().strip(),
        "fkpr": agent_data[10].get_text().strip(),
        "fdpr": agent_data[11].get_text().strip(),
        "k": agent_data[12].get_text().strip(),
        "d": agent_data[13].get_text().strip(),
        "a": agent_data[14].get_text().strip(),
        "fk": agent_data[15].get_text().strip(),
        "fd": agent_data[16].get_text().strip(),
    }

    # VLR, why would you put `nan` here instead of simply putting a 0?
    if response["fdpr"] == "nan":
        response["fdpr"] = 0

    return response

