import asyncio

import dateutil.parser
import httpx
from bs4 import BeautifulSoup, element

from app import schemas
from app.constants import NEWS_URL, PREFIX
from app.utils import fix_datetime_tz


async def news_list() -> list[schemas.NewsItem]:
    """
    Function to parse a list of matches from the VLR.gg homepage
    :return: The parsed matches
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(NEWS_URL)

    soup = BeautifulSoup(response.content, "lxml")

    return list(await asyncio.gather(*[parse_news(news) for news in soup.find_all("a", class_="wf-module-item")]))


async def parse_news(data: element.Tag) -> schemas.NewsItem:
    title, description, metadata = [item.get_text().strip() for item in data.find_all("div")[0].find_all("div")]
    metadata = metadata.split("•")
    return schemas.NewsItem(
        url=f"{PREFIX}{data['href']}",
        title=title,
        description=description,
        author=metadata[-1].replace("by", "").strip(),
        date=fix_datetime_tz(dateutil.parser.parse(metadata[1].strip(), ignoretz=True)),
    )
