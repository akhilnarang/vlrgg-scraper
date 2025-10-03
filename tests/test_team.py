import time
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from app.services import team


@pytest.mark.asyncio
async def test_get_team_data():
    # Load the fixtures
    main_path = Path(__file__).parent / "fixtures" / "team_2.html"
    upcoming_path = Path(__file__).parent / "fixtures" / "team_2_upcoming.html"
    completed_path = Path(__file__).parent / "fixtures" / "team_2_completed.html"

    with open(main_path, "r", encoding="utf-8") as f:
        main_html = f.read()
    with open(upcoming_path, "r", encoding="utf-8") as f:
        upcoming_html = f.read()
    with open(completed_path, "r", encoding="utf-8") as f:
        completed_html = f.read()

    # Mock responses in order: main, upcoming, completed
    main_response = AsyncMock()
    main_response.status_code = 200
    main_response.content = main_html.encode("utf-8")

    upcoming_response = AsyncMock()
    upcoming_response.status_code = 200
    upcoming_response.content = upcoming_html.encode("utf-8")

    completed_response = AsyncMock()
    completed_response.status_code = 200
    completed_response.content = completed_html.encode("utf-8")

    with patch("httpx.AsyncClient.get", side_effect=[main_response, upcoming_response, completed_response]):
        start = time.time()
        result = await team.get_team_data("2")
        parse_time = time.time() - start
        print(f"Team parsing time: {parse_time:.4f} seconds")

    # Basic assertions
    assert result.name == "Sentinels"
    assert result.tag == "SEN"
    assert isinstance(result.roster, list)
    assert len(result.roster) > 0
    assert isinstance(result.upcoming, list)
    assert isinstance(result.completed, list)
