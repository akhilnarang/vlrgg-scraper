import asyncio

import httpx
from bs4 import BeautifulSoup, element

from app import schemas
from app.constants import PLAYER_URL
from app.utils import clean_number_string, get_image_url


async def get_player_data(id: str) -> schemas.Player:
    """
    Function get a player's data from VLR and return a parsed version
    :param id: The player's ID
    :return: The parsed data
    """

    async with httpx.AsyncClient() as client:
        response = await client.get(PLAYER_URL.format(id))

    soup = BeautifulSoup(response.content, "lxml")
    player_info = soup.find("div", class_="player-header")
    player_summary_container_1 = soup.find("div", class_="player-summary-container-1")
    player_summary_container_2 = soup.find("div", class_="player-summary-container-2")

    player_data = {
        "alias": player_info.find("h1").get_text().strip(),
        "name": player_info.find("h2").get_text().strip(),
        "img": get_image_url(player_info.find("img")["src"]),
        "country": player_info.find("div", class_="ge-text-light").get_text().strip(),
        "agents": await asyncio.gather(
            *[parse_agent_data(agent.find_all("td")) for agent in agent_data.find_all("tr") if agent]
        )
        if (agent_data := soup.find("tbody"))
        else [],
    }

    for header in player_summary_container_2.find_all("h2"):
        if header.get_text().strip().lower() == "event placements":
            player_data["total_winnings"] = (
                header.find_next("div").find("div").find("span").get_text()[1:].replace(",", "")
            )
            break

    for header in player_summary_container_1.find_all("h2"):
        match header.get_text().strip().lower():
            case "current teams":
                current_team = header.find_next(name="div").find("a")
                player_data["current_team"] = {
                    "id": current_team["href"].split("/")[-2],
                    "name": current_team.find_all("div")[1].find("div").get_text().strip(),
                    "img": get_image_url(current_team.find("img")["src"]),
                }
            case "past teams":
                player_data["past_teams"] = [
                    {
                        "id": current_team["href"].split("/")[-2],
                        "name": current_team.find_all("div")[1].find("div").get_text().strip(),
                        "img": get_image_url(current_team.find("img")["src"]),
                    }
                    for current_team in header.find_next(name="div").find_all("a")
                ]

    for link in player_info.find_all("a"):
        if "twitter.com" in link["href"]:
            player_data["twitter"] = link.get_text().strip()
        elif "twitch.tv" in link["href"]:
            player_data["twitch"] = link["href"]
    return schemas.Player.model_validate(player_data)


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
        "img": get_image_url(img["src"]),
        "count": count.replace("(", "").replace(")", ""),
        "percent": percent[:-1],
        "rounds": clean_number_string(agent_data[2].get_text()),
        "rating": clean_number_string(agent_data[3].get_text()),
        "acs": clean_number_string(agent_data[4].get_text()),
        "kd": clean_number_string(agent_data[5].get_text()),
        "adr": clean_number_string(agent_data[6].get_text()),
        "kast": clean_number_string(agent_data[7].get_text()),
        "kpr": clean_number_string(agent_data[8].get_text()),
        "apr": clean_number_string(agent_data[9].get_text()),
        "fkpr": clean_number_string(agent_data[10].get_text()),
        "fdpr": clean_number_string(agent_data[11].get_text()),
        "k": clean_number_string(agent_data[12].get_text()),
        "d": clean_number_string(agent_data[13].get_text()),
        "a": clean_number_string(agent_data[14].get_text()),
        "fk": clean_number_string(agent_data[15].get_text()),
        "fd": clean_number_string(agent_data[16].get_text()),
    }

    return response
