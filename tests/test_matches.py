from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

import app.constants as constants
from app.services import matches


@pytest.mark.asyncio
async def test_match_list_keeps_first_upcoming_date_group(monkeypatch):
    fixture_dir = Path(__file__).parent / "fixtures"
    upcoming_html = (fixture_dir / "matches.html").read_bytes()
    results_html = (fixture_dir / "matches_results.html").read_bytes()

    response_by_url = {
        constants.UPCOMING_MATCHES_URL: _mock_response(constants.UPCOMING_MATCHES_URL, upcoming_html),
        constants.PAST_MATCHES_URL: _mock_response(constants.PAST_MATCHES_URL, results_html),
    }

    async def mock_get(url: str, *args, **kwargs):
        return response_by_url[url]

    mock_redis = AsyncMock()
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        result = await matches.match_list(mock_redis)

    result_by_id = {match.id: match for match in result}

    assert result_by_id["684611"].team1.name == "FULL SENSE"
    assert result_by_id["684611"].team2.name == "FUT Esports"
    assert result_by_id["684612"].team1.name == "LEVIATÁN"
    assert result_by_id["684612"].team2.name == "Global Esports"
    assert result_by_id["684610"].team1.name == "Team Vitality"
    assert result_by_id["684610"].team2.name == "Dragon Ranger Gaming"


def _mock_response(url: str, content: bytes) -> AsyncMock:
    response = AsyncMock()
    response.status_code = 200
    response.content = content
    response.url = url
    return response


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
