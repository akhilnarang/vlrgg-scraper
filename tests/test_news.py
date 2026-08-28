from pathlib import Path
from unittest.mock import patch

import pytest

from app import constants
from app.exceptions import ScrapingError
from app.services import news

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_news_list_returns_full_history_in_order(http_get):
    pages = {
        constants.NEWS_URL: (FIXTURE_DIR / "news_page1.html").read_bytes(),
        news.news_url(2): (FIXTURE_DIR / "news_page2.html").read_bytes(),
    }
    with patch(
        "httpx.AsyncClient.get",
        side_effect=http_get(pages, fallback=(FIXTURE_DIR / "news_empty.html").read_bytes()),
    ):
        result = await news.news_list(pages=0)

    assert len(result) == 60
    assert len({item.url for item in result}) == 60
    assert result[0].title
    assert result[30].title


@pytest.mark.asyncio
async def test_news_list_does_not_return_partial_results(http_get):
    pages = {constants.NEWS_URL: (FIXTURE_DIR / "news_page1.html").read_bytes()}
    failures = {news.news_url(2): 500}

    with patch("httpx.AsyncClient.get", side_effect=http_get(pages, failures=failures)), pytest.raises(ScrapingError):
        await news.news_list(pages=2)


@pytest.mark.asyncio
async def test_news_article_preserves_links_and_quoted_names(http_response):
    response = http_response(
        "https://www.vlr.gg/562934",
        (FIXTURE_DIR / "news_562934.html").read_bytes(),
    )

    with patch("httpx.AsyncClient.get", return_value=response):
        result = await news.news_by_id("562934")

    assert result.title == "EDward Gaming bids farewell to head coach Muggle"
    assert 'Tang "{{link_2}}" Shijun' in result.content
    assert len(result.links) == 21
    assert len(result.images) == 1
    assert result.author == "raezeri"
