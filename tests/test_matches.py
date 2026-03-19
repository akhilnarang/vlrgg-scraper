import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from app.services import matches


@pytest.mark.asyncio
async def test_match_list():
    # Mock the HTTP responses for upcoming and past matches
    mock_response_upcoming = AsyncMock()
    mock_response_upcoming.status_code = 200
    mock_response_upcoming.content = (
        b'<html><body><div class="wf-label">Upcoming</div><div class="wf-card"></div></body></html>'
    )

    mock_response_past = AsyncMock()
    mock_response_past.status_code = 200
    mock_response_past.content = (
        b'<html><body><div class="wf-label">Past</div><div class="wf-card"></div></body></html>'
    )

    # Mock Redis client
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", side_effect=[mock_response_upcoming, mock_response_past]):
        result = await matches.match_list(mock_redis)

    # Assertions
    assert isinstance(result, list)
    # Since the mock HTML has no actual matches, the list should be empty
    assert len(result) == 0


@pytest.mark.asyncio
async def test_match_by_id():
    # Load the fixture HTML for match
    fixture_path = Path(__file__).parent / "fixtures" / "match_12345.html"
    with open(fixture_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Mock the HTTP response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.content = html_content.encode("utf-8")

    # Mock Redis client
    mock_redis = AsyncMock()

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await matches.match_by_id("12345", mock_redis)

    # Assertions — verify all fields parsed via find() (Phase 6 conversion)
    assert result.teams is not None
    assert isinstance(result.teams, list)

    # Event data (get_event_data uses find() for series, stage, img)
    assert result.event is not None
    assert result.event.id != ""
    assert result.event.series != ""
    assert result.event.stage != ""
    assert result.event.img is not None

    # Videos
    assert result.videos is not None
    assert isinstance(result.videos.streams, list)
    assert isinstance(result.videos.vods, list)

    # Map data (get_map_data uses find() for round_number; parse_scoreboard uses find() for player data)
    if result.data:
        for map_data in result.data:
            assert map_data.map != ""
            assert len(map_data.teams) == 2
            for member in map_data.members:
                assert member.name != ""
                assert member.team != ""
                assert isinstance(member.agents, list)
            for round_info in map_data.rounds:
                assert isinstance(round_info.round_number, int)
