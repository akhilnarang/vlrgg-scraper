import asyncio

import httpx
from bs4 import BeautifulSoup, element

from app import schemas
from app.constants import PREFIX


async def news_list() -> list[schemas.NewsItem]:
    """
    Function to parse a list of matches from the VLR.gg homepage
    :return: The parsed matches
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(PREFIX)

    soup = BeautifulSoup(response.content, "html.parser")

    return list(
        await asyncio.gather(
            *[parse_news(news) for news in soup.find_all("a", class_="wf-module-item news-item mod-first")]
        )
    )


async def parse_news(data: element.Tag) -> schemas.NewsItem:
    return schemas.NewsItem(
        id=data["href"].split("/")[1], title=data.find_all("div", class_="news-item-title")[0].get_text().strip()
    )
