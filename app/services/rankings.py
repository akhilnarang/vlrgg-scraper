import asyncio

import httpx
from bs4 import BeautifulSoup

from app import schemas, utils
from app.constants import RANKING_URL_REGION, RANKINGS_URL


async def ranking_list(limit: int) -> list[schemas.NewsItem]:
    """
    Function to parse a list of rankings from the VLR.gg rankgs page

    :param limit: The number of teams you want data about
    :return: The parsed ranks
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(RANKINGS_URL)

    soup = BeautifulSoup(response.content, "html.parser")

    return list(
        await asyncio.gather(
            *[
                parse_rankings(region["href"], limit)
                for region in soup.find("div", class_="wf-nav mod-collapsible").find_all("a")[1:]
            ]
        )
    )


async def parse_rankings(path: str, limit: int) -> schemas.Ranking:
    async with httpx.AsyncClient() as client:
        response = await client.get(RANKING_URL_REGION.format(path))

    soup = BeautifulSoup(response.content, "html.parser")

    return schemas.Ranking(
        region=" ".join(path.split("/")[-1].split("-")).title(),
        teams=[
            schemas.TeamRanking(
                name=team.find("a")["data-sort-value"],
                id=team.find("a")["href"].split("/")[2],
                logo=utils.get_image_url(team.find("img")["src"]),
                rank=team.find("div", class_="rank-item-rank").get_text(),
                points=team.find("div", class_="rank-item-rating").get_text().strip(),
            )
            for team in soup.find_all("div", class_="rank-item wf-card fc-flex")[:limit]
        ],
    )
