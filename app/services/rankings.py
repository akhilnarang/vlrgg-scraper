import asyncio
import http

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from app import schemas, utils
from app.constants import RANKING_URL_REGION, RANKINGS_URL, REGION_NAME_MAPPING


async def ranking_list() -> list[schemas.Ranking]:
    """
    Function to parse a list of rankings from the VLR.gg rankings page

    :return: The parsed ranks
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(RANKINGS_URL)
        if response.status_code != http.HTTPStatus.OK:
            raise HTTPException(status_code=response.status_code, detail="VLR.gg server returned an error")

    soup = BeautifulSoup(response.content, "lxml")

    data = list(
        await asyncio.gather(
            *[
                parse_rankings(region["href"])
                for region in soup.find("div", class_="wf-nav mod-collapsible").find_all("a")[1:]
                if not region["href"].endswith("/gc")
            ]
        )
    )
    return data


async def parse_rankings(path: str) -> schemas.Ranking:
    """
    Function to parse team data from a region's ranking page

    :param path: The path to the region's page on VLR
    :return: The parsed data
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(RANKING_URL_REGION.format(path))
        if response.status_code != http.HTTPStatus.OK:
            raise HTTPException(status_code=response.status_code, detail="VLR.gg server returned an error")

    soup = BeautifulSoup(response.content, "lxml")

    region_name = path.split("/")[-1]
    region_name = REGION_NAME_MAPPING.get(region_name.lower()) or " ".join(region_name.split("-")).title()

    return schemas.Ranking(
        region=region_name,
        teams=[
            schemas.TeamRanking(
                name=team.find("a")["data-sort-value"].strip(),
                id=team.find("a")["href"].split("/")[2],
                logo=utils.get_image_url(team.find("img")["src"]),
                rank=utils.clean_string(team.find("div", class_="rank-item-rank").get_text()),
                points=utils.clean_string(team.find("div", class_="rank-item-rating").get_text()),
                country=utils.clean_string(team.find("div", class_="rank-item-team-country").get_text()),
            )
            for team in soup.find_all("div", class_="rank-item wf-card fc-flex")[:25]
        ],
    )
