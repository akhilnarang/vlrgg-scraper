import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from sqlalchemy import text

from app.services import events
from app.constants import EventStatus
from app.exceptions import ScrapingError
from app.core.connections import async_session


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

    # Load the fixture HTML for matches
    fixture_path_matches = Path(__file__).parent / "fixtures" / "event_2283_matches.html"
    with open(fixture_path_matches, "r", encoding="utf-8") as f:
        matches_html_content = f.read()

    # Mock the matches response
    mock_response_matches = AsyncMock()
    mock_response_matches.status_code = 200
    mock_response_matches.content = matches_html_content.encode("utf-8")

    with patch(
        "httpx.AsyncClient.get",
        side_effect=[mock_response_matches, mock_response_event, mock_response_event, mock_response_event],
    ):
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

    # DB assertions
    async with async_session() as session:
        # Event inserted
        event_result = await session.execute(text("SELECT * FROM events WHERE id = '2283'"))
        event_row = event_result.fetchone()
        assert event_row is not None
        assert event_row.title == "Valorant Champions 2025"

        # Teams inserted from matches stages
        teams_count = await session.execute(text("SELECT count(*) FROM teams"))
        assert teams_count.scalar() > 0

        # Event-teams inserted
        event_teams_count = await session.execute(text("SELECT count(*) FROM event_teams WHERE event_id = '2283'"))
        assert event_teams_count.scalar() > 0


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
