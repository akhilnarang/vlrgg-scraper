import asyncio
import http
import os
from concurrent.futures import ProcessPoolExecutor

from bs4 import BeautifulSoup
from app.exceptions import ScrapingError

from app import schemas, utils
import app.constants as constants
from app.core.connections import get_http_client


async def ranking_list() -> list[schemas.Ranking]:
    """
    Function to parse a list of rankings from the VLR.gg rankings page

    :return: The parsed ranks
    """
    async with get_http_client() as client:
        response = await client.get(constants.RANKINGS_URL)
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

    soup = BeautifulSoup(response.content, "lxml")

    region_paths = [
        region["href"]
        for region in soup.find("div", class_="wf-nav mod-collapsible").find_all("a")[1:]
        if not region["href"].endswith("/gc")
    ]

    if not region_paths:
        return []

    # Fetch all region pages concurrently (I/O parallelism via asyncio)
    responses = await asyncio.gather(*[_fetch_region(path) for path in region_paths])

    # Parse all pages in parallel (CPU parallelism via ProcessPoolExecutor)
    # Each page is 1-14 MB of HTML, parsing takes 100ms-2s per page
    loop = asyncio.get_running_loop()
    n_workers = min(len(region_paths), os.cpu_count() or 4)
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        results = await asyncio.gather(
            *[
                loop.run_in_executor(pool, _parse_ranking_page, path, html)
                for path, html in zip(region_paths, responses)
            ]
        )

    return list(results)


async def _fetch_region(path: str) -> bytes:
    """Fetch a region's ranking page HTML."""
    async with get_http_client() as client:
        response = await client.get(constants.RANKING_URL_REGION.format(path))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
    return response.content


def _parse_ranking_page(path: str, content: bytes) -> schemas.Ranking:
    """Parse a region's ranking page. Runs in a subprocess for CPU parallelism."""
    soup = BeautifulSoup(content, "lxml")

    region_name = path.split("/")[-1]
    region_name = constants.REGION_NAME_MAPPING.get(region_name.lower()) or " ".join(region_name.split("-")).title()

    return schemas.Ranking(
        region=region_name,
        teams=[
            schemas.TeamRanking(
                name=team.find("a")["data-sort-value"].strip(),
                id=team.find("a")["href"].split("/")[2],
                logo=utils.get_image_url(team.find("img")["src"]),  # type: ignore
                rank=int(utils.clean_number_string(team.find("div", class_="rank-item-rank").get_text())),
                points=int(utils.clean_number_string(team.find("div", class_="rank-item-rating").get_text())),
                country=utils.clean_string(team.find("div", class_="rank-item-team-country").get_text()),
            )
            for team in soup.find_all("div", class_="rank-item wf-card fc-flex")[:25]
        ],
    )
