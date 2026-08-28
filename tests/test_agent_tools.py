from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.tools import build_tools


@pytest.mark.asyncio
async def test_count_team_matches_returns_a_consistent_filtered_record():
    team = SimpleNamespace(
        model_dump=lambda mode=None: {
            "completed": [
                {"opponent": "A", "event": "Masters", "stage": "GF", "score": "3:1", "date": "2026-01-01"},
                {"opponent": "B", "event": "Masters", "stage": "GF", "score": "0:3", "date": "2026-01-02"},
                {"opponent": "C", "event": "Masters", "stage": "GF", "score": "W:FF", "date": "2026-01-03"},
                {"opponent": "D", "event": "League", "stage": "GF", "score": "2:0", "date": "2026-01-04"},
                {"opponent": "E", "event": "Masters", "stage": "W1", "score": "2:0", "date": "2026-01-05"},
            ]
        }
    )
    _, dispatch = build_tools(redis_client=None)

    with patch("app.agent.tools.team.get_team_data", new=AsyncMock(return_value=team)):
        result = await dispatch["count_team_matches"](id="624", event="Masters", stage="GF")

    assert result == {"played": 3, "won": 1, "lost": 1, "other": 1}
