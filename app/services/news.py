import asyncio
import http
import re

import dateutil.parser
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from app.exceptions import ScrapingError

from app import schemas
import app.constants as constants
from app.core.connections import get_http_client
from app.utils import expand_url, fix_datetime_tz, get_image_url


# VLR returns 30 news cards per page. When fetching "all" pages we request them in
# batches of this size and stop as soon as a page yields no cards.
NEWS_PAGE_BATCH_SIZE = 5


def _collapse_link_quote_padding(text: str) -> str:
    result = []
    last_end = 0
    for match in re.finditer(r"\s+({{link_\d+}})\s+", text):
        prev_char = text[match.start() - 1] if match.start() > 0 else ""
        next_char = text[match.end()] if match.end() < len(text) else ""
        is_quoted_link = (prev_char == next_char == '"') or (prev_char == "“" and next_char == "”")
        if not is_quoted_link:
            continue

        result.append(text[last_end : match.start()])
        result.append(match.group(1))
        last_end = match.end()

    if not result:
        return text

    result.append(text[last_end:])
    return "".join(result)


def normalize_article_text(text: str) -> str:
    text = re.sub(r'\s+([.,;:!?])', r'\1', text)
    return _collapse_link_quote_padding(text)


def extract_text_and_links(element: Tag, counter: int) -> tuple[str, list[dict[str, str]], int]:
    """
    Recursively extracts text and links from a BeautifulSoup element.

    :param element: The BeautifulSoup Tag to process
    :param counter: Current counter for link placeholders
    :return: Tuple of (processed_text, links_list, updated_counter)
    """
    text_parts = []
    local_links = []
    for child in element.children:
        if isinstance(child, NavigableString):
            t = " ".join(child.strip().split())
            if t:
                text_parts.append(t)
        elif isinstance(child, Tag):
            if child.name == "a":
                link_text = " ".join(child.get_text().strip().split())
                href = child.get("href")
                if link_text and href:
                    text_parts.append(f"{{{{link_{counter}}}}}")  # Double braces for literal
                    local_links.append({"text": link_text, "url": expand_url(str(href))})
                    counter += 1
            elif child.name in ["span", "strong", "em", "b", "i", "br"]:  # Inline tags
                sub_text, sub_links, counter = extract_text_and_links(child, counter)
                text_parts.append(sub_text)
                local_links.extend(sub_links)
            else:
                # For other tags, get text
                t = " ".join(child.get_text().strip().split())
                if t:
                    text_parts.append(t)
    return normalize_article_text(" ".join(text_parts)), local_links, counter


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


async def news_by_id(id: str) -> schemas.NewsArticle:
    """
    Function to fetch a news article by ID from VLR
    :param id: The news article ID
    :return: The parsed news article
    """
    async with get_http_client() as client:
        response = await client.get(constants.NEWS_URL_WITH_ID.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise ScrapingError(url=str(response.url), upstream_status=response.status_code)

    soup = BeautifulSoup(response.content, "lxml")

    # Parse the article content
    article_container = soup.find("div", class_="wf-card mod-article")

    title = ""
    content = ""
    links = []
    images = []
    videos = []
    author = ""
    date = None
    counter = 0

    if article_container:
        # Title
        if title_elem := article_container.find("h1", class_="wf-title mod-article-title"):
            title = title_elem.get_text().strip()

        # Content
        content_div = (
            soup.find("div", class_="content")
            or soup.find("div", class_="article-body")
            or soup.find("article")
            or soup.find("main")
        )
        if content_div:
            # Remove hover cards to avoid duplicate links
            for hover in content_div.find_all("span", class_="wf-hover-card"):
                hover.decompose()
            for element in content_div.find_all(["p", "h2", "h3", "ul", "ol"]):
                if element.name in ["p", "h2", "h3"]:
                    text, new_links, counter = extract_text_and_links(element, counter)
                    content += text + "\n\n"
                    links.extend(new_links)
                elif element.name == "ul":
                    for li in element.find_all("li"):
                        text, new_links, counter = extract_text_and_links(li, counter)
                        if text.strip():
                            content += f"- {text}\n"
                            links.extend(new_links)
                    content += "\n"
                elif element.name == "ol":
                    for i, li in enumerate(element.find_all("li")):
                        text, new_links, counter = extract_text_and_links(li, counter)
                        if text.strip():
                            content += f"{i + 1}. {text}\n"
                            links.extend(new_links)
                    content += "\n"
            # Check for standalone images/videos
            for img in content_div.find_all("img"):
                if src := img.get("src"):
                    src_str = str(src)
                    content += "{{image_{}}}\n\n".format(len(images))
                    images.append(get_image_url(src_str))
            for vid in content_div.find_all(["iframe", "video"]):
                if src := vid.get("src"):
                    src_str = str(src)
                    content += "{{video_{}}}\n\n".format(len(videos))
                    videos.append(get_image_url(src_str))

        # Metadata
        if meta_div := article_container.find("div", class_="article-meta"):
            if author_elem := meta_div.find("a", class_="article-meta-author"):
                author = author_elem.get_text().strip()

            if date_elem := meta_div.find("span", class_="js-date-toggle"):
                if date_elem.get("title"):
                    try:
                        date = fix_datetime_tz(dateutil.parser.parse(str(date_elem["title"]), ignoretz=True))
                    except Exception:
                        pass

    # Fallback for title
    if not title:
        if title_tag := soup.find("title"):
            title = title_tag.get_text().strip()
    else:
        # Fallback: try to find title and content anywhere
        if title_elem := soup.find("h1") or soup.find("title"):
            title = title_elem.get_text().strip()

        # Look for main content
        content_div = soup.find("div", class_="content") or soup.find("article") or soup.find("main")
        if content_div:
            for element in content_div.find_all(["p", "h2", "h3", "ul", "ol"]):
                if element.name in ["p", "h2", "h3"]:
                    text, new_links, counter = extract_text_and_links(element, counter)
                    content += text + "\n\n"
                    links.extend(new_links)
                elif element.name == "ul":
                    for li in element.find_all("li"):
                        text, new_links, counter = extract_text_and_links(li, counter)
                        if text.strip():
                            content += f"- {text}\n"
                            links.extend(new_links)
                    content += "\n"
                elif element.name == "ol":
                    for i, li in enumerate(element.find_all("li")):
                        text, new_links, counter = extract_text_and_links(li, counter)
                        if text.strip():
                            content += f"{i + 1}. {text}\n"
                            links.extend(new_links)
                    content += "\n"

    # Parse metadata - try different selectors
    if meta_div := soup.find("div", class_="article-meta") or soup.find("div", class_="meta"):
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
                try:
                    date = fix_datetime_tz(dateutil.parser.parse(str(date_elem.get_text().strip()), ignoretz=True))
                except Exception:
                    pass

    # If still no title, try page title
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text().strip()

    return schemas.NewsArticle(
        id=id,
        title=title,
        content=content.strip(),
        links=links,
        images=images,
        videos=videos,
        date=date,
        author=author,
    )
