import asyncio
import http

import dateutil.parser
from bs4 import BeautifulSoup, Tag
from bs4.element import ResultSet
from app.exceptions import ScrapingError

from app import schemas, utils, cache
import app.constants as constants
from app.core.connections import get_http_client
from app.utils import clean_number_string, expand_url, get_image_url


# VLR returns 50 match-history cards per page. When fetching "all" pages we request
# them in batches of this size and stop as soon as a page yields no cards.
PLAYER_MATCHES_PAGE_BATCH_SIZE = 5


async def get_player_data(id: str, match_pages: int = 1) -> schemas.Player:
    """
    Function get a player's data from VLR and return a parsed version
    :param id: The player's ID
    :param match_pages: How many pages of match history to fold into the response (VLR serves
        50 per page). Defaults to ``1`` (the first 50 recent matches). A value ``<= 0`` fetches
        the player's FULL match history. See :func:`get_player_matches`.
    :return: The parsed data
    """

    # Short-TTL cache (no cron for by-id pages): collapses duplicate live fetches.
    cache_key = f"player:{id}:{match_pages}"
    if cached := await cache.get(cache_key):
        return schemas.Player.model_validate_json(cached)

    async with get_http_client() as client:
        response, matches = await asyncio.gather(
            client.get(constants.PLAYER_URL.format(id)),
            get_player_matches(id, pages=match_pages),
        )
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

    soup = BeautifulSoup(response.content, "lxml")
    player_info = soup.find("div", class_="player-header")
    player_summary_container_1 = soup.find("div", class_="player-summary-container-1")
    player_summary_container_2 = soup.find("div", class_="player-summary-container-2")

    player_data = {
        "alias": player_info.find("h1").get_text().strip(),
        "name": player_info.find("h2").get_text().strip(),
        "img": get_image_url(player_info.find("img")["src"]),
        "country": player_info.find("div", class_="ge-text-light").get_text().strip(),
        "agents": [parse_agent_data(agent.find_all("td")) for agent in agent_data.find_all("tr") if agent]
        if (agent_data := soup.find("tbody"))
        else [],
        "matches": matches,
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
            player_data["twitter"] = expand_url(link.get_text().strip())
        elif "twitch.tv" in link["href"]:
            player_data["twitch"] = expand_url(link["href"])
    result = schemas.Player.model_validate(player_data)
    await cache.set(cache_key, result.model_dump_json(), ttl=constants.CACHE_TTL_PLAYER)
    return result


def parse_agent_data(agent_data: ResultSet) -> dict:
    """
    Function to parse agent data from a player's page on VLR
    :param agent_data: An agent table row
    :return: The parsed data
    """
    img = agent_data[0].find("img")
    count, percent = agent_data[1].get_text().strip().split(" ")
    rounds = clean_number_string(agent_data[2].get_text())

    # VLR replaced the separate FKPR/FDPR columns with FK:FD and swapped KAST/ADR.
    # Keep the API's existing per-round fields by deriving them from the raw totals.
    if len(agent_data) == 16:
        adr_index, kast_index = 7, 6
        k_index, d_index, a_index, fk_index, fd_index = 11, 12, 13, 14, 15
        fk = clean_number_string(agent_data[fk_index].get_text())
        fd = clean_number_string(agent_data[fd_index].get_text())
        fkpr = round(fk / rounds, 2) if rounds else 0
        fdpr = round(fd / rounds, 2) if rounds else 0
    else:
        adr_index, kast_index = 6, 7
        k_index, d_index, a_index, fk_index, fd_index = 12, 13, 14, 15, 16
        fkpr = clean_number_string(agent_data[10].get_text())
        fdpr = clean_number_string(agent_data[11].get_text())
        fk = clean_number_string(agent_data[fk_index].get_text())
        fd = clean_number_string(agent_data[fd_index].get_text())

    response = {
        "name": img["alt"],
        "img": get_image_url(img["src"]),
        "count": count.replace("(", "").replace(")", ""),
        "percent": percent[:-1],
        "rounds": rounds,
        "rating": clean_number_string(agent_data[3].get_text()),
        "acs": clean_number_string(agent_data[4].get_text()),
        "kd": clean_number_string(agent_data[5].get_text()),
        "adr": clean_number_string(agent_data[adr_index].get_text()),
        "kast": clean_number_string(agent_data[kast_index].get_text()),
        "kpr": clean_number_string(agent_data[8].get_text()),
        "apr": clean_number_string(agent_data[9].get_text()),
        "fkpr": fkpr,
        "fdpr": fdpr,
        "k": clean_number_string(agent_data[k_index].get_text()),
        "d": clean_number_string(agent_data[d_index].get_text()),
        "a": clean_number_string(agent_data[a_index].get_text()),
        "fk": fk,
        "fd": fd,
    }

    return response


async def get_player_matches(id: str, pages: int = 1) -> list[schemas.PlayerMatch]:
    """
    Function to get a player's match history from VLR and return a parsed version.

    :param id: The player's ID
    :param pages: How many pages of match history to fetch (VLR serves 50 per page).
        Defaults to ``1`` (the first 50 matches, newest first). A value ``> 1`` fetches
        pages ``1..pages`` concurrently. A value ``<= 0`` fetches ALL pages, requesting
        more in batches until a page returns no matches. Newest-first ordering is preserved.
    :return: The parsed matches, newest first
    """

    async with get_http_client() as client:
        response = await client.get(player_matches_url(id, 1))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

        # Page 1 has been fetched above; grab any additional pages while the client is open.
        matches = parse_player_matches(response.content)
        if matches and pages != 1:
            seen: set[str] = {m["id"] for m in matches}
            # Clamp bounded mode; full-history mode cap is enforced inside the helper.
            effective_pages = min(pages, constants.MAX_PAGINATION_PAGES) if pages >= 1 else pages
            matches.extend(await fetch_additional_player_matches(client, id, effective_pages, seen))

    return [schemas.PlayerMatch.model_validate(match) for match in matches]


def player_matches_url(id: str, page: int) -> str:
    """Build the URL for a given page of a player's match history."""
    return f"{constants.PLAYER_MATCHES_URL.format(id)}/?page={page}"


def parse_player_matches(content: bytes) -> list[dict]:
    """Parse all match-history cards from a single page of HTML."""
    soup = BeautifulSoup(content, "lxml")
    return [parse_player_match(match) for match in soup.find_all("a", class_="wf-card fc-flex m-item")]


async def fetch_additional_player_matches(
    client, id: str, pages: int, seen: set[str] | None = None
) -> list[dict]:
    """
    Fetch match-history pages beyond page 1, preserving order (page 2, then 3, ...).

    :param client: The shared HTTP client.
    :param id: The player's ID.
    :param pages: Total pages wanted (already clamped by caller for bounded mode).
        ``> 1`` fetches pages ``2..pages`` concurrently; ``<= 0`` fetches every remaining
        page in batches up to ``MAX_PAGINATION_PAGES`` total, stopping once a page returns
        no cards or contributes no new ids. A non-200 on any page raises ScrapingError
        rather than returning a partial history (which could be cached and undercount).
    :param seen: Set of match ids already collected (page 1). New items are filtered against
        this set in all modes; full-history mode also stops when a page adds zero new ids.
    :return: The parsed matches from the additional pages, in order.
    """
    matches: list[dict] = []
    if seen is None:
        seen = set()

    if pages > 1:
        # Fetch pages 2..N in batches (not one big fan-out) to bound concurrent load on VLR.
        stop = False
        for start in range(2, pages + 1, PLAYER_MATCHES_PAGE_BATCH_SIZE):
            batch = range(start, min(start + PLAYER_MATCHES_PAGE_BATCH_SIZE, pages + 1))
            responses = await asyncio.gather(*(client.get(player_matches_url(id, p)) for p in batch))
            for response in responses:
                if response.status_code != http.HTTPStatus.OK:
                    raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
                page_matches = parse_player_matches(response.content)
                if not page_matches:
                    stop = True
                    break
                new = [m for m in page_matches if m["id"] not in seen]
                seen.update(m["id"] for m in new)
                matches.extend(new)
            if stop:
                break
        return matches

    # pages <= 0: fetch all remaining pages in batches until empty, zero new ids,
    # or MAX_PAGINATION_PAGES total pages (including the already-fetched page 1) is reached.
    page = 2
    pages_crawled = 1  # page 1 already counted
    while pages_crawled < constants.MAX_PAGINATION_PAGES:
        batch_size = min(PLAYER_MATCHES_PAGE_BATCH_SIZE, constants.MAX_PAGINATION_PAGES - pages_crawled)
        batch = list(range(page, page + batch_size))
        responses = await asyncio.gather(*(client.get(player_matches_url(id, p)) for p in batch))
        pages_crawled += len(batch)
        stop = False
        for response in responses:
            if response.status_code != http.HTTPStatus.OK:
                raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
            page_matches = parse_player_matches(response.content)
            if not page_matches:
                stop = True
                break
            new = [m for m in page_matches if m["id"] not in seen]
            if not new:
                stop = True
                break
            seen.update(m["id"] for m in new)
            matches.extend(new)
        if stop:
            break
        page += batch_size

    return matches


def parse_player_match(match_data: Tag) -> dict:
    """
    Function to parse a single match-history card from a player's matches page on VLR.

    The first team listed on the card is the player's team for that match, the second is
    the opponent.

    :param match_data: The HTML data (a ``wf-card fc-flex m-item`` anchor)
    :return: The parsed data
    """
    event, *stage = [
        f
        for f in match_data.find("div", class_="m-item-event text-of").get_text().strip().replace("\t", "").split("\n")
        if f
    ]
    teams = match_data.find_all("div", class_="m-item-team")
    response = {
        "id": utils.get_href(match_data["href"]).split("/")[1],
        "event": event,
        "stage": "".join(stage),
        "team": utils.clean_string(teams[0].find("span", class_="m-item-team-name").get_text()),
        "opponent": utils.clean_string(teams[1].find("span", class_="m-item-team-name").get_text())
        if len(teams) > 1
        else "",
    }
    if score := match_data.find("div", class_="m-item-result"):
        response["score"] = utils.clean_string(score.get_text())
    else:
        response["score"] = ""

    # Roster-"core" tags (e.g. "#ACM"): teams[0] is the player's team, teams[1] the opponent.
    if teams and (core := teams[0].find("div", class_="m-item-team-core")):
        response["roster_core"] = utils.clean_string(core.get_text())
    if len(teams) > 1 and (opp_core := teams[1].find("div", class_="m-item-team-core")):
        response["opponent_roster_core"] = utils.clean_string(opp_core.get_text())

    response["date"] = utils.fix_datetime_tz(
        dateutil.parser.parse(match_data.find("div", class_="m-item-date").get_text(), ignoretz=True)
    )
    return response
