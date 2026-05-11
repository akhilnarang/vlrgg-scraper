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


def _build_event_card(status_class: str, status_text: str) -> BeautifulSoup:
    html = f"""
    <a class="wf-card mod-flex event-item" href="/event/9999/some-event">
      <div class="event-item-inner">
        <div class="event-item-title">Some Event</div>
        <div>
          <div class="event-item-desc-item">
            <span class="event-item-desc-item-status {status_class}">{status_text}</span>
          </div>
          <div class="event-item-desc-item mod-prize">$1,000</div>
          <div class="event-item-desc-item mod-dates">Jan 1—Feb 1</div>
          <div class="event-item-desc-item mod-location">
            <i class="flag mod-kr"></i>
          </div>
        </div>
        <div class="event-item-thumb">
          <img src="//owcdn.net/img/test.png"/>
        </div>
      </div>
    </a>
    """
    return BeautifulSoup(html, "lxml").find("a", class_="wf-card")


@pytest.mark.asyncio
async def test_parse_event_paused_status():
    event_tag = _build_event_card("mod-paused", "paused")
    mock_redis = AsyncMock()

    result = await events.parse_event(event_tag, mock_redis)

    assert result.status == EventStatus.PAUSED
    assert result.id == "9999"
    assert result.title == "Some Event"


@pytest.mark.asyncio
async def test_parse_event_unknown_status_falls_back(caplog):
    event_tag = _build_event_card("mod-suspended", "suspended")
    mock_redis = AsyncMock()

    import logging

    with caplog.at_level(logging.WARNING):
        result = await events.parse_event(event_tag, mock_redis)

    assert result.status == EventStatus.UNKNOWN
    assert any("Unknown VLR event status" in record.message for record in caplog.records)


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
