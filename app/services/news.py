import asyncio
import http
from datetime import datetime

import dateutil.parser
from bs4 import BeautifulSoup, Tag

from app import constants, schemas
from app.core.connections import get_http_client
from app.exceptions import ScrapingError
from app.services.news_content import parse_article_blocks, project_legacy_fields
from app.utils import fix_datetime_tz

# VLR returns 30 news cards per page. When fetching "all" pages we request them in
# batches of this size and stop as soon as a page yields no cards.
NEWS_PAGE_BATCH_SIZE = 5


def news_url(page: int) -> str:
    """Build the URL for a given page of the VLR.gg news list."""
    return f"{constants.NEWS_URL}?page={page}"


def parse_news_list(content: bytes) -> list[schemas.NewsItem]:
    """Parse all news cards from a single page of HTML."""
    soup = BeautifulSoup(content, "lxml")
    return [parse_news(news) for news in soup.find_all("a", class_="wf-module-item")]


async def fetch_additional_news(client, pages: int, seen: set[str] | None = None) -> list[schemas.NewsItem]:
    """
    Fetch news beyond page 1, preserving order (page 2, then 3, ...).

    :param client: The shared HTTP client.
    :param pages: Total pages wanted (already clamped by caller for bounded mode).
        ``> 1`` fetches pages ``2..pages`` concurrently; ``<= 0`` fetches every remaining
        page in batches up to ``MAX_PAGINATION_PAGES`` total, stopping once a page returns
        no items or contributes no new urls. A non-200 on any page raises ScrapingError
        rather than returning a partial list (page 1 is already validated by the caller).
    :param seen: Set of news item urls already collected (page 1). New items are filtered
        against this set in all modes; full-history mode also stops when a page adds none.
        NewsItem uses ``url`` as its natural unique key (no separate id field).
    :return: The parsed news items from the additional pages, in order.
    """
    items: list[schemas.NewsItem] = []
    if seen is None:
        seen = set()

    if pages > 1:
        # Fetch pages 2..N in batches (not one big fan-out) to bound concurrent load on VLR.
        stop = False
        for start in range(2, pages + 1, NEWS_PAGE_BATCH_SIZE):
            batch = range(start, min(start + NEWS_PAGE_BATCH_SIZE, pages + 1))
            responses = await asyncio.gather(*(client.get(news_url(p)) for p in batch))
            for response in responses:
                if response.status_code != http.HTTPStatus.OK:
                    raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
                page_items = parse_news_list(response.content)
                if not page_items:
                    stop = True
                    break
                new = [item for item in page_items if item.url not in seen]
                seen.update(item.url for item in new)
                items.extend(new)
            if stop:
                break
        return items

    # pages <= 0: fetch all remaining pages in batches until empty, zero new urls,
    # or MAX_PAGINATION_PAGES total pages (including the already-fetched page 1) is reached.
    page = 2
    pages_crawled = 1  # page 1 already counted
    while pages_crawled < constants.MAX_PAGINATION_PAGES:
        batch_size = min(NEWS_PAGE_BATCH_SIZE, constants.MAX_PAGINATION_PAGES - pages_crawled)
        batch = list(range(page, page + batch_size))
        responses = await asyncio.gather(*(client.get(news_url(p)) for p in batch))
        pages_crawled += len(batch)
        stop = False
        for response in responses:
            if response.status_code != http.HTTPStatus.OK:
                raise ScrapingError(url=str(response.url), upstream_status=response.status_code)
            page_items = parse_news_list(response.content)
            if not page_items:
                stop = True
                break
            new = [item for item in page_items if item.url not in seen]
            if not new:
                stop = True
                break
            seen.update(item.url for item in new)
            items.extend(new)
        if stop:
            break
        page += batch_size

    return items


async def news_list(pages: int = 1) -> list[schemas.NewsItem]:
    """
    Function to parse a list of news items from the VLR.gg news page
    :param pages: How many pages of news to fetch (VLR serves 30 per page).
        Defaults to ``1`` (the first page, preserving the previous behaviour).
        A value ``<= 0`` fetches ALL pages, requesting more until a page returns no items.
    :return: The parsed news items
    """
    async with get_http_client() as client:
        response = await client.get(constants.NEWS_URL)
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

        # Page 1 has been fetched above; grab any additional pages while the client is open.
        news_items = parse_news_list(response.content)
        if news_items and pages != 1:
            seen: set[str] = {item.url for item in news_items}
            # Clamp bounded mode; full-history mode cap is enforced inside the helper.
            effective_pages = min(pages, constants.MAX_PAGINATION_PAGES) if pages >= 1 else pages
            news_items.extend(await fetch_additional_news(client, effective_pages, seen))

    return news_items


def parse_news(data: Tag) -> schemas.NewsItem:
    title, description, metadata = [item.get_text().strip() for item in data.find("div").find_all("div")]
    metadata = metadata.split("•")
    return schemas.NewsItem(
        url=f"{constants.PREFIX}{data['href']}",
        title=title,
        description=description,
        author=metadata[-1].replace("by", "").strip(),
        date=fix_datetime_tz(dateutil.parser.parse(metadata[1].strip(), ignoretz=True)),
    )


def _parse_article_date(value: str) -> datetime | None:
    try:
        return fix_datetime_tz(dateutil.parser.parse(value, ignoretz=True))
    except ValueError, OverflowError:
        return None


async def news_by_id(id: str) -> schemas.NewsArticle:
    """Fetch and parse a news article by ID from VLR.

    :param id: The news article ID.
    :return: The parsed article with ordered blocks and legacy projections.
    :raises ScrapingError: If VLR returns a non-200 HTTP status.
    """
    async with get_http_client() as client:
        response = await client.get(constants.NEWS_URL_WITH_ID.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

    soup = BeautifulSoup(response.content, "lxml")

    # Parse the article content
    article_container = soup.find("div", class_="wf-card mod-article")

    title = ""
    author = ""
    date = None
    title_elem = None
    meta_div = None

    if article_container:
        # Title
        if title_elem := article_container.find("h1", class_="wf-title mod-article-title"):
            title = title_elem.get_text().strip()

        # Metadata
        if meta_div := article_container.find("div", class_="article-meta"):
            if author_elem := meta_div.find("a", class_="article-meta-author"):
                author = author_elem.get_text().strip()

            if (date_elem := meta_div.find(class_="js-date-toggle")) and date_elem.get("title"):
                date = _parse_article_date(str(date_elem["title"]))

    if not title:
        title_elem = soup.find("h1") or soup.find("title")
        if title_elem:
            title = title_elem.get_text().strip()

    # Parse metadata - try different selectors
    if not (author and date) and (
        meta_div := soup.find("div", class_="article-meta") or soup.find("div", class_="meta")
    ):
        if not author:
            if author_elem := meta_div.find("span", class_="author"):
                author = author_elem.get_text().strip().replace("by ", "").replace("By ", "")
            else:
                # Try to find author in text
                meta_text = meta_div.get_text()
                if "by" in meta_text.lower():
                    author = meta_text.split("by")[-1].strip()

        if not date:
            date_elem = meta_div.find("span", class_="date") or meta_div.find("time")
            if date_elem:
                date = _parse_article_date(date_elem.get_text().strip())

    body_div = (
        (article_container.find("div", class_="article-body") if article_container else None)
        or soup.find("div", class_="article-body")
        or soup.find("div", class_="content")
    )
    fallback_container = soup.find("article") or soup.find("main")
    content_div = body_div or fallback_container

    if not body_div and fallback_container:
        if title_elem and any(p is fallback_container for p in title_elem.parents):
            header = title_elem.find_parent("header")
            if header and any(p is fallback_container for p in header.parents):
                header.extract()
            else:
                title_elem.extract()
        if meta_div and any(p is fallback_container for p in meta_div.parents):
            meta_div.extract()

    blocks = parse_article_blocks(content_div) if content_div else []
    legacy = project_legacy_fields(blocks)

    return schemas.NewsArticle(
        id=id,
        title=title,
        content=legacy.content,
        blocks=blocks,
        links=legacy.links,
        images=legacy.images,
        videos=legacy.videos,
        date=date,
        author=author,
    )
