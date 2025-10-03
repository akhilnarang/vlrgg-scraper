import time
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from app.services import news


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
    assert isinstance(result.links, list)
    assert len(result.links) == 21
    assert isinstance(result.images, list)
    assert len(result.images) == 1
    assert isinstance(result.videos, list)
    assert len(result.videos) == 0
    assert result.author == "raezeri"


@pytest.mark.asyncio
async def test_news_list_current():
    # Load the current fixture HTML
    fixture_path = Path(__file__).parent / "fixtures" / "news_current.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        start = time.time()
        result = await news.news_list()
        parse_time = time.time() - start
        print(f"Parsing time: {parse_time:.4f} seconds")

    # Assertions based on parsed data
    assert len(result) == 30

    # Check first news item
    first_news = result[0]
    assert first_news.title == "NRG sweeps FNATIC, earns grand final spot in Paris"
    assert (
        first_news.description
        == "NRG swept FNATIC 2-0 in the upper bracket final of Valorant Champions 2025, defeating an old teammate and earning their organization's first international finals appearance."
    )
    assert first_news.url == "https://www.vlr.gg/564283/nrg-sweeps-fnatic-earns-grand-final-spot-in-paris"
    assert first_news.author == "weivy"

    # Check last news item
    last_news = result[-1]
    assert last_news.title == "GIANTX continues miracle run to Champs playoffs, defeats XLG"
    assert (
        last_news.description
        == "GIANTX swept Xi Lai Gaming 2-0 in the decider match for Group A at Valorant Champions 2025, qualifying for the playoff stage in their first international in 2025."
    )
    assert last_news.url == "https://www.vlr.gg/554251/giantx-continues-miracle-run-to-champs-playoffs-defeats-xlg"
    assert last_news.author == "weivy"
