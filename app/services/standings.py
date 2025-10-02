import http
import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException
from pydantic import HttpUrl

from app import schemas, utils
from app.constants import STANDINGS_URL


async def standings_list(year: int) -> schemas.Standings:
    """
    Function to parse standings from the VLR.gg standings page for a given year

    :param year: The VCT year
    :return: The parsed standings
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(STANDINGS_URL.format(year))
        if response.status_code != http.HTTPStatus.OK:
            raise HTTPException(status_code=response.status_code, detail="VLR.gg server returned an error")

    soup = BeautifulSoup(response.content, "lxml")

    circuits = []
    for group in soup.find_all("div", class_="eg-standing-group"):
        region = group.find("div", class_="wf-label").get_text().strip()
        table = group.find("table")
        teams = []
        rank = 1
        for row in table.find_all("tr")[1:]:  # Skip header
            team_td = row.find("td", class_="eg-standing-group-team")
            if team_td:
                a = team_td.find("a")
                href = a["href"]
                id = int(href.split("/")[2])
                img = a.find("img")
                logo = HttpUrl(utils.get_image_url(img["src"]))
                name_div = a.find("div", class_="text-of")
                name = name_div.find("div", recursive=False).get_text().strip()
                country = name_div.find_all("div")[1].get_text().strip()
                points_td = row.find_all("td")[1]
                points = int(points_td.get_text().strip().split()[0])
                teams.append(
                    schemas.TeamStanding(name=name, id=id, logo=logo, rank=rank, points=points, country=country)
                )
                rank += 1
        circuits.append(schemas.CircuitStanding(region=region, teams=teams))

    return schemas.Standings(year=year, circuits=circuits)
