import asyncio
import itertools

import httpx
from bs4 import BeautifulSoup, element

from app import schemas
from app.constants import EVENTS_URL


async def get_events() -> list[schemas.Event]:
    """
    Fetch a list of events from VLR, and return the parsed response
    :return: Parsed list of events
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(EVENTS_URL)

    soup = BeautifulSoup(response.content, "html.parser")
    return list(
        itertools.chain(
            *(
                await asyncio.gather(
                    *[convert_to_list(data) for data in soup.find_all("div", class_="events-container-col")]
                )
            )
        )
    )


async def convert_to_list(events: element.Tag) -> list[schemas.Event]:
    """
    Parse a list of events
    :param events: The events
    :return: The list of parsed events
    """
    return list(await asyncio.gather(*[parse_event(event) for event in events.find_all("a", class_="wf-card")]))


async def parse_event(event: element.Tag) -> schemas.Event:
    """
    Parse an event
    :param event: The HTML
    :return: The event parsed
    """
    event_id = event["href"].split("/")[2]
    title = event.find_all("div", class_="event-item-title")[0].get_text().strip()
    status = event.find_all("span", class_="event-item-desc-item-status")[0].get_text().strip()
    prize = event.find_all("div", class_="mod-prize")[0].get_text().strip().replace("\t", "").split("\n")[0]
    dates = event.find_all("div", class_="mod-dates")[0].get_text().strip().replace("\t", "").split("\n")[0]
    location = (
        event.find_all("div", class_="mod-location")[0]
        .find_all("i", class_="flag")[0]
        .get("class")[1]
        .replace("mod-", "")
    )
    img = event.find_all("div", class_="event-item-thumb")[0].find("img")["src"]
    if img == "/img/vlr/tmp/vlr.png":
        img = "https://www.vlr.gg" + img
    else:
        img = "https:" + img
    return schemas.Event(id=event_id, title=title, status=status, prize=prize, dates=dates, location=location, img=img)
