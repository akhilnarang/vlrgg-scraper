import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from bs4 import BeautifulSoup

from app.services import events
from app.constants import EventStatus
from app.exceptions import ScrapingError


@pytest.mark.asyncio
async def test_get_events():
    # Load the fixture HTML
    fixture_path = Path(__file__).parent / "fixtures" / "events.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    # Mock Redis client
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await events.get_events(mock_redis)

    # Assertions
    assert len(result) == 1

    # Check first event
    first_event = result[0]
    assert first_event.id == "2283"
    assert first_event.title == "Valorant Champions 2025"
    assert first_event.status == EventStatus.ONGOING
    assert first_event.prize == "$2,250,000"
    assert first_event.dates == "Sep 12—Oct 6"
    assert first_event.location == "fr"
    assert str(first_event.img) == "https://owcdn.net/img/63067806d167d.png"


@pytest.mark.asyncio
async def test_get_event_by_id():
    # Load the fixture HTML for event
    fixture_path = Path(__file__).parent / "fixtures" / "event_2283.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP responses
    mock_response_event = AsyncMock()
    mock_response_event.status_code = 200
    mock_response_event.content = html_content.encode("utf-8")

    # For matches, use a simple mock since we don't have a fixture
    mock_response_matches = AsyncMock()
    mock_response_matches.status_code = 200
    mock_response_matches.content = b"<html><body></body></html>"

    with patch("httpx.AsyncClient.get", side_effect=[mock_response_event, mock_response_matches]):
        result = await events.get_event_by_id("2283")

    # Assertions
    assert result.id == "2283"
    assert result.title == "Valorant Champions 2025"
    assert result.status == EventStatus.ONGOING
    assert result.prize == "$2,250,000 USD"
    assert result.dates == "Sep 12, 2025 - Oct 6, 2025"
    assert result.location == "Accor Arena, Paris"
    assert str(result.img) == "https://owcdn.net/img/63067806d167d.png"
    assert isinstance(result.matches, list)


@pytest.mark.asyncio
async def test_get_events_scraping_error():
    """Test that ScrapingError is raised when VLR.gg returns a bad response."""
    # Mock the HTTP response with bad status
    mock_response = AsyncMock()
    mock_response.status_code = 500

    # Mock Redis client
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        with pytest.raises(ScrapingError) as exc_info:
            await events.get_events(mock_redis)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "VLR.gg server returned an error"


def test_parse_event_standings_with_logo_first_column_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "event_2760.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, "lxml")
    standings_container = soup.find("div", class_="event-container")

    result = events.parse_event_standings(standings_container)

    assert len(result) >= 2
    assert result[0]["team"] == "Xi Lai Gaming"
    assert result[0]["country"] == "China"
    assert result[0]["wins"] == 0
    assert result[0]["losses"] == 0
    assert all(standing["team"] != "Spoiler hidden" for standing in result)
