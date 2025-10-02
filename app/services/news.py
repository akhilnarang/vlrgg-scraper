import asyncio
import http

import dateutil.parser
import httpx
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from fastapi import HTTPException

from app import schemas
import app.constants as constants
from app.utils import expand_url, fix_datetime_tz, get_image_url


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
    full_text = " ".join(text_parts)
    return full_text, local_links, counter


async def news_list() -> list[schemas.NewsItem]:
    """
    Function to parse a list of matches from the VLR.gg homepage
    :return: The parsed matches
    """
    async with httpx.AsyncClient(timeout=constants.REQUEST_TIMEOUT) as client:
        response = await client.get(constants.NEWS_URL)
        if response.status_code != http.HTTPStatus.OK:
            raise HTTPException(status_code=response.status_code, detail="VLR.gg server returned an error")

    soup = BeautifulSoup(response.content, "lxml")

    return list(await asyncio.gather(*[parse_news(news) for news in soup.find_all("a", class_="wf-module-item")]))


async def parse_news(data: Tag) -> schemas.NewsItem:
    title, description, metadata = [item.get_text().strip() for item in data.find_all("div")[0].find_all("div")]
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
    async with httpx.AsyncClient(timeout=constants.REQUEST_TIMEOUT) as client:
        response = await client.get(constants.NEWS_URL_WITH_ID.format(id))
        if response.status_code != http.HTTPStatus.OK:
            raise HTTPException(status_code=response.status_code, detail="VLR.gg server returned an error")

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
