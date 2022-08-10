import asyncio
import json

import httpx
from bs4 import BeautifulSoup

from app import cache, schemas, utils
from app.constants import RANKING_URL_REGION, RANKINGS_URL


async def ranking_list(limit: int) -> list[schemas.Ranking]:
    """
    Function to parse a list of rankings from the VLR.gg rankgs page

    :param limit: The number of teams you want data about
    :return: The parsed ranks
    """
    key = f"rankings|{limit}"
    try:
        return [schemas.Ranking.parse_obj(ranking) for ranking in json.loads(await cache.get(key))]
    except cache.CacheMiss:
        async with httpx.AsyncClient() as client:
            response = await client.get(RANKINGS_URL)

        soup = BeautifulSoup(response.content, "lxml")

        data = list(
            await asyncio.gather(
                *[
                    parse_rankings(region["href"], limit)
                    for region in soup.find("div", class_="wf-nav mod-collapsible").find_all("a")[1:]
                ]
            )
        )
        await cache.set(key, json.dumps([item.dict() for item in data]), 3600)
        return data


async def parse_rankings(path: str, limit: int) -> schemas.Ranking:
    """
    Function to parse team data from a region's ranking page

    :param path: The path to the region's page on VLR
    :param limit: The number of teams we want data about
    :return: The parsed data
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(RANKING_URL_REGION.format(path))

    soup = BeautifulSoup(response.content, "lxml")

    return schemas.Ranking(
        region=" ".join(path.split("/")[-1].split("-")).title(),
        teams=[
            schemas.TeamRanking(
                name=team.find("a")["data-sort-value"],
                id=team.find("a")["href"].split("/")[2],
                logo=utils.get_image_url(team.find("img")["src"]),
                rank=team.find("div", class_="rank-item-rank").get_text(),
                points=team.find("div", class_="rank-item-rating").get_text().strip(),
                country=team.find("div", class_="rank-item-team-country").get_text().strip(),
            )
            for team in soup.find_all("div", class_="rank-item wf-card fc-flex")[:limit]
        ],
    )
