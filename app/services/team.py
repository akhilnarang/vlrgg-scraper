import asyncio
import http

import dateutil.parser
from bs4 import BeautifulSoup, Tag
from app.exceptions import ScrapingError

from app import schemas, utils, cache
import app.constants as constants
from app.core.connections import get_http_client


# VLR returns 50 completed match cards per page. When fetching "all" pages we request
# them in batches of this size and stop as soon as a page yields no cards.
COMPLETED_PAGE_BATCH_SIZE = 5


async def get_team_data(id: str, completed_pages: int = 1) -> schemas.Team:
    """
    Function get a team's data from VLR and return a parsed version
    :param id: The team's ID
    :param completed_pages: How many pages of COMPLETED matches to fetch (VLR serves 50 per page).
        Defaults to ``1`` (the first 50 completed matches, preserving the previous behaviour).
        A value ``<= 0`` fetches ALL pages, requesting more until a page returns no matches.
        Upcoming matches are always limited to a single page.
    :return: The parsed data
    """

    # Short-TTL cache (no cron for by-id pages): collapses duplicate live fetches.
    cache_key = f"team:{id}:{completed_pages}"
    if cached := await cache.get(cache_key):
        return schemas.Team.model_validate_json(cached)

    async with get_http_client() as client:
        response, upcoming_matches_response, completed_matches_response = await asyncio.gather(
            client.get(constants.TEAM_URL.format(id)),
            client.get(constants.TEAM_UPCOMING_MATCHES_URL.format(id)),
            client.get(constants.TEAM_COMPLETED_MATCHES_URL.format(id)),
        )
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

        if upcoming_matches_response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(
                url=str(upcoming_matches_response.url), upstream_status=upcoming_matches_response.status_code
            )

        if completed_matches_response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(
                url=str(completed_matches_response.url), upstream_status=completed_matches_response.status_code
            )

        # Page 1 has been fetched above; grab any additional pages while the client is open.
        completed_match_list = parse_completed_matches(completed_matches_response.content)
        if completed_match_list and completed_pages != 1:
            seen: set[str] = {m["id"] for m in completed_match_list}
            # Clamp bounded mode; full-history mode cap is enforced inside the helper.
            effective_pages = (
                min(completed_pages, constants.MAX_PAGINATION_PAGES) if completed_pages >= 1 else completed_pages
            )
            completed_match_list.extend(await fetch_additional_completed_matches(client, id, effective_pages, seen))

    soup = BeautifulSoup(response.content, "lxml")
    upcoming_matches = BeautifulSoup(upcoming_matches_response.content, "lxml")

    team_info = soup.find("div", class_="team-header")
    name = utils.clean_string(team_info.find("h1").get_text())
    img = utils.get_image_url(team_info.find("img")["src"])

    tag = ""
    website = None
    twitter = None

    if tag_element := team_info.find("h2"):
        tag = utils.clean_string(tag_element.get_text())

    for link in soup.find("div", class_="team-header-links").find_all("a"):
        if link := link.get("href"):
            link = utils.expand_url(link)
            if link and "twitter.com" in link:
                twitter = link
            else:
                website = link

    country = utils.clean_string(soup.find("div", class_="team-header-country").get_text())

    team_data = soup.find("div", class_="team-summary-container-1")

    # Rank doesn't show up on VLR sometimes - not sure why. So we default to 0 if we can't find it.
    if rank_div := team_data.find("div", class_="rank-num mod-"):
        rank = utils.clean_number_string(rank_div.get_text())
    else:
        rank = 0

    if region_div := team_data.find("div", class_="rating-txt"):
        region = utils.clean_string(region_div.get_text())
    else:
        region = ""

    roster = [parse_player(player) for player in team_data.find_all("div", class_="team-roster-item")]
    upcoming_match_list = [
        parse_match(match) for match in upcoming_matches.find_all("a", class_="wf-card fc-flex m-item")
    ]
    result = schemas.Team.model_validate(
        {
            "name": name,
            "tag": tag,
            "img": img,
            "website": website,
            "twitter": twitter,
            "country": country,
            "rank": rank,
            "region": region,
            "roster": roster,
            "upcoming": upcoming_match_list,
            "completed": completed_match_list,
        }
    )
    await cache.set(cache_key, result.model_dump_json(), ttl=constants.CACHE_TTL_TEAM)
    return result


def completed_matches_url(id: str, page: int) -> str:
    """Build the URL for a given page of a team's completed matches."""
    return f"{constants.TEAM_COMPLETED_MATCHES_URL.format(id)}&page={page}"


def parse_completed_matches(content: bytes) -> list[dict]:
    """Parse all completed-match cards from a single page of HTML."""
    soup = BeautifulSoup(content, "lxml")
    return [parse_match(match) for match in soup.find_all("a", class_="wf-card fc-flex m-item")]


async def fetch_additional_completed_matches(
    client, id: str, completed_pages: int, seen: set[str] | None = None
) -> list[dict]:
    """
    Fetch completed matches beyond page 1, preserving order (page 2, then 3, ...).

    :param client: The shared HTTP client.
    :param id: The team's ID.
    :param completed_pages: Total pages wanted (already clamped by caller for bounded mode).
        ``> 1`` fetches pages ``2..completed_pages`` concurrently; ``<= 0`` fetches every
        remaining page in batches up to ``MAX_PAGINATION_PAGES`` total, stopping once a page
        returns no cards or contributes no new ids. A non-200 on any page raises ScrapingError
        rather than returning a partial history (which could be cached and silently undercount).
    :param seen: Set of match ids already collected (page 1). New items are filtered against
        this set in all modes; full-history mode also stops when a page adds zero new ids.
    :return: The parsed matches from the additional pages, in order.
    """
    matches: list[dict] = []
    if seen is None:
        seen = set()

    if completed_pages > 1:
        # Fetch pages 2..N in batches (not one big fan-out) to bound concurrent load on VLR.
        stop = False
        for start in range(2, completed_pages + 1, COMPLETED_PAGE_BATCH_SIZE):
            batch = range(start, min(start + COMPLETED_PAGE_BATCH_SIZE, completed_pages + 1))
            responses = await asyncio.gather(*(client.get(completed_matches_url(id, p)) for p in batch))
            for response in responses:
                if response.status_code != http.HTTPStatus.OK:
                    raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
                page_matches = parse_completed_matches(response.content)
                if not page_matches:
                    stop = True
                    break
                new = [m for m in page_matches if m["id"] not in seen]
                seen.update(m["id"] for m in new)
                matches.extend(new)
            if stop:
                break
        return matches

    # completed_pages <= 0: fetch all remaining pages in batches until empty, zero new ids,
    # or MAX_PAGINATION_PAGES total pages (including the already-fetched page 1) is reached.
    page = 2
    pages_crawled = 1  # page 1 already counted
    while pages_crawled < constants.MAX_PAGINATION_PAGES:
        batch_size = min(COMPLETED_PAGE_BATCH_SIZE, constants.MAX_PAGINATION_PAGES - pages_crawled)
        batch = list(range(page, page + batch_size))
        responses = await asyncio.gather(*(client.get(completed_matches_url(id, p)) for p in batch))
        pages_crawled += len(batch)
        stop = False
        for response in responses:
            if response.status_code != http.HTTPStatus.OK:
                raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
            page_matches = parse_completed_matches(response.content)
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


def parse_player(player_data: Tag) -> dict:
    """
    Function to parse a player's data from VLR
    :param player_data: The HTML data
    :return: The parsed data
    """
    response = {
        "id": utils.get_href(player_data.find("a")["href"]).split("/")[2],
        "alias": utils.clean_string(player_data.find("div", class_="team-roster-item-name-alias").get_text()),
        "img": utils.get_image_url(player_data.find("div", class_="team-roster-item-img").find("img")["src"]),
    }
    if name_div := player_data.find("div", class_="team-roster-item-name-real"):
        response["name"] = utils.clean_string(name_div.get_text())

    if role := player_data.find("div", class_="team-roster-item-name-role"):
        response["role"] = utils.clean_string(role.get_text())
    elif role := player_data.find("i", class_="fa fa-star"):
        response["role"] = utils.clean_string(role["title"])
    return response


def parse_match(match_data: Tag) -> dict:
    """
    Function to parse a match's data from VLR
    :param match_data: The HTML data
    :return: The parsed data
    """
    event, *stage = [
        f
        for f in match_data.find("div", class_="m-item-event text-of").get_text().strip().replace("\t", "").split("\n")
        if f
    ]
    response = {
        "event": event,
        "stage": "".join(stage),
        "id": utils.get_href(match_data["href"]).split("/")[1],
    }
    if eta := match_data.find("span", class_="rm-item-score-eta"):
        response["eta"] = utils.clean_string(eta.get_text())
        response["opponent"] = utils.clean_string(
            eta.find_next("div").find_next("div").find_next("div").get_text().strip().split("\n")[0]
        )
    elif score := match_data.find("div", class_="m-item-result"):
        response["score"] = utils.clean_string(score.get_text())
        opponent_div = score.find_next("div").find_next("div").find_next("div")
        if "ff" in response["score"].lower():
            response["opponent"] = utils.clean_string(opponent_div.get_text().strip().split("\n")[0])
        else:
            response["opponent"] = utils.clean_string(opponent_div.find_next("div").get_text().strip().split("\n")[0])

    if "score" not in response:
        response["score"] = ""

    # Roster-"core" tags (e.g. "#ACM"): m-item-team[0] is this team, [1] the opponent.
    teams = match_data.find_all("div", class_="m-item-team")
    if teams and (core := teams[0].find("div", class_="m-item-team-core")):
        response["roster_core"] = utils.clean_string(core.get_text())
    if len(teams) > 1 and (opp_core := teams[1].find("div", class_="m-item-team-core")):
        response["opponent_roster_core"] = utils.clean_string(opp_core.get_text())

    response["date"] = utils.fix_datetime_tz(
        dateutil.parser.parse(match_data.find("div", class_="m-item-date").get_text(), ignoretz=True)
    )
    return response
