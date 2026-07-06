import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

import app.constants as constants
from app.services import news

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _mock_response(url: str, content: bytes) -> AsyncMock:
    response = AsyncMock()
    response.status_code = 200
    response.content = content
    response.url = url
    return response


def _build_mock_get():
    page1_html = (FIXTURE_DIR / "news_page1.html").read_bytes()
    page2_html = (FIXTURE_DIR / "news_page2.html").read_bytes()
    empty_html = (FIXTURE_DIR / "news_empty.html").read_bytes()

    response_by_url = {
        # Page 1 is fetched without an explicit ?page param by news_list.
        constants.NEWS_URL: page1_html,
        news.news_url(2): page2_html,
    }

    async def mock_get(url: str, *args, **kwargs):
        # Any page beyond what we have a fixture for is treated as empty (out of range).
        content = response_by_url.get(url, empty_html)
        return _mock_response(url, content)

    return mock_get


@pytest.mark.asyncio
async def test_news_list():
    # Load the fixture HTML
    fixture_path = Path(__file__).parent / "fixtures" / "news.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await news.news_list()

    # Assertions
    assert len(result) == 3

    # Check first news item
    first_news = result[0]
    assert first_news.title == "Team Vitality allows UNFAKE to explore options"
    assert first_news.description == "Vitality's newest IGL is in search of new opportunities."
    assert first_news.url == "https://www.vlr.gg/562952/team-vitality-allows-unfake-to-explore-options"
    assert first_news.author == "raezeri"

    # Check second news item
    second_news = result[1]
    assert second_news.title == "Sentinels win major tournament"
    assert second_news.description == "Sentinels dominate the competition with outstanding performance."
    assert second_news.url == "https://www.vlr.gg/562953/sentinels-win-major-tournament"
    assert second_news.author == "vlrnews"

    # Check third news item
    third_news = result[2]
    assert third_news.title == "New agent revealed in latest patch"
    assert third_news.description == "Riot unveils a new agent with unique abilities."
    assert third_news.url == "https://www.vlr.gg/562954/new-agent-revealed"
    assert third_news.author == "riotgames"


@pytest.mark.asyncio
async def test_news_by_id():
    # Load the fixture HTML for news article
    fixture_path = Path(__file__).parent / "fixtures" / "news_562952.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await news.news_by_id("562952")

    # Assertions
    assert result.id == "562952"
    assert result.title == "Team Vitality allows UNFAKE to explore options"
    assert isinstance(result.content, str)
    assert "Vitality" in result.content
    assert isinstance(result.links, list)
    assert len(result.links) == 0
    assert isinstance(result.images, list)
    assert len(result.images) == 0
    assert isinstance(result.videos, list)
    assert len(result.videos) == 0
    assert result.author == "raezeri"


@pytest.mark.asyncio
async def test_news_list_default_single_page():
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await news.news_list()

    # Default behaviour: only the first page of news (30 items).
    assert len(result) == 30
    assert all(item.title for item in result)
    assert all(item.url.startswith(constants.PREFIX) for item in result)


@pytest.mark.asyncio
async def test_news_list_multi_page_returns_more_items():
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        default = await news.news_list()
        two_pages = await news.news_list(pages=2)

    # Two pages should yield roughly twice as many news items as the default.
    assert len(two_pages) == 60
    assert len(two_pages) > len(default)

    # Ordering is preserved: page 1 first, then page 2 appended after.
    assert [n.url for n in two_pages[:30]] == [n.url for n in default]
    # No duplicates across the combined pages and parsing stayed intact.
    urls = [n.url for n in two_pages]
    assert len(set(urls)) == len(urls)
    assert all(n.title for n in two_pages)


@pytest.mark.asyncio
async def test_news_list_fetch_all_pages_stops_on_empty():
    # pages <= 0 means "fetch all"; only pages 1 and 2 have items here, the batch walk
    # should stop once it hits the empty out-of-range pages.
    with patch("httpx.AsyncClient.get", side_effect=_build_mock_get()):
        result = await news.news_list(pages=0)

    assert len(result) == 60


@pytest.mark.asyncio
async def test_news_list_later_page_error_raises_not_partial():
    """A non-200 on a later page must raise ScrapingError, never return a partial list."""
    from app.exceptions import ScrapingError

    page1_html = (FIXTURE_DIR / "news_page1.html").read_bytes()

    async def mock_get(url: str, *args, **kwargs):
        if url == constants.NEWS_URL:
            return _mock_response(url, page1_html)
        resp = _mock_response(url, b"")
        resp.status_code = 500
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        with pytest.raises(ScrapingError):
            await news.news_list(pages=0)


@pytest.mark.asyncio
async def test_news_by_id_562934():
    # Load the fixture HTML for news article 562934
    fixture_path = Path(__file__).parent / "fixtures" / "news_562934.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await news.news_by_id("562934")

    # Assertions
    assert result.id == "562934"
    assert result.title == "EDward Gaming bids farewell to head coach Muggle"
    assert isinstance(result.content, str)
    assert "Muggle" in result.content
    assert 'Tang "{{link_2}}" Shijun' in result.content
    assert '" {{link_2}} "' not in result.content
    assert "}} ." not in result.content
    assert "}} ," not in result.content
    assert isinstance(result.links, list)
    assert len(result.links) == 21
    assert isinstance(result.images, list)
    assert len(result.images) == 1
    assert isinstance(result.videos, list)
    assert len(result.videos) == 0
    assert result.author == "raezeri"
