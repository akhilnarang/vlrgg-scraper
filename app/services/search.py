import asyncio
import http

import httpx
from bs4 import BeautifulSoup, element
from fastapi import HTTPException

from app import schemas, constants, utils


async def get_data(search_category: constants.SearchCategory, search_term: str) -> list[schemas.SearchResult]:
    """
    Function to return data based on a user specified search term

    :param search_category: The category the user wants to search for
    :param search_term: The search term
    :return: The parsed data
    """

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(constants.SEARCH_URL.format(search_term, search_category))
        if response.status_code != http.HTTPStatus.OK:
            raise HTTPException(status_code=response.status_code, detail="VLR.gg server returned an error")

    soup = BeautifulSoup(response.content, "lxml")
    return await asyncio.gather(*[parse_result(result) for result in soup.find_all("a", class_="search-item")])


async def parse_result(result_data: element.Tag) -> schemas.SearchResult:
    """
    Function to parse the search result data

    :param result_data: A search item from VLR search page
    :return: The data parsed
    """
    url = result_data["href"]
    if "team" in url:
        category = constants.SearchCategory.TEAM
    elif "event" in url:
        category = constants.SearchCategory.EVENT
    elif "player" in url:
        category = constants.SearchCategory.PLAYER
    elif "series" in url:
        category = constants.SearchCategory.SERIES
    else:
        raise Exception("Unknown category")

    if description := result_data.find("div", class_="search-item-desc"):
        description = utils.clean_string(description.text.strip())

    return schemas.SearchResult.model_validate(
        {
            "id": url.split("/")[-2],
            "img": utils.get_image_url(result_data.find("img")["src"]),
            "name": utils.clean_string(result_data.find("div", class_="search-item-title").text.strip()),
            "category": category,
            "description": description,
        }
    )
