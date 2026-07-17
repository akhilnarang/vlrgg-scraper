import pytest
from unittest.mock import AsyncMock, patch
from app.agent.tools import build_tools


@pytest.mark.asyncio
async def test_redis_gated_tools_absent_without_client():
    schemas, dispatch = build_tools(redis_client=None)
    names = {s["name"] if "name" in s else s["function"]["name"] for s in schemas}
    assert "search" in names and "get_team" in names
    assert "get_events" not in names and "get_matches" not in names


@pytest.mark.asyncio
async def test_redis_gated_tools_present_with_client():
    schemas, dispatch = build_tools(redis_client=object())
    names = {s["name"] if "name" in s else s["function"]["name"] for s in schemas}
    assert "get_events" in names and "get_matches" in names


@pytest.mark.asyncio
async def test_count_team_matches_counts_wins_losses():
    fake_team = type("T", (), {})()
    fake_team.model_dump = lambda mode=None: {
        "name": "PRX",
        "completed": [
            {
                "opponent": "A",
                "event": "Masters X",
                "stage": "Playoffs ⋅GF",
                "score": "3:1",
                "date": "2025-06-22T00:00:00+00:00",
            },
            {
                "opponent": "B",
                "event": "Masters Y",
                "stage": "Playoffs ⋅GF",
                "score": "0:3",
                "date": "2026-03-15T00:00:00+00:00",
            },
            {
                "opponent": "C",
                "event": "VCT PAC",
                "stage": "Group Stage ⋅W1",
                "score": "2:0",
                "date": "2025-01-01T00:00:00+00:00",
            },
        ],
        "upcoming": [],
    }
    _, dispatch = build_tools(redis_client=None)
    with patch("app.agent.tools.team.get_team_data", new=AsyncMock(return_value=fake_team)):
        out = await dispatch["count_team_matches"](id="624", stage="GF")
    assert out == {"played": 2, "won": 1, "lost": 1, "other": 0}


@pytest.mark.asyncio
async def test_count_team_matches_forfeits_keep_played_consistent():
    """Unclassifiable scores (forfeits/empty) go to `other`, so played == won+lost+other."""
    fake_team = type("T", (), {})()
    fake_team.model_dump = lambda mode=None: {
        "name": "PRX",
        "completed": [
            {"opponent": "A", "event": "E", "stage": "GS", "score": "2:0", "date": "2025-06-22T00:00:00+00:00"},
            {"opponent": "B", "event": "E", "stage": "GS", "score": "0:2", "date": "2025-06-23T00:00:00+00:00"},
            {"opponent": "C", "event": "E", "stage": "GS", "score": "W:FF", "date": "2025-06-24T00:00:00+00:00"},
            {"opponent": "D", "event": "E", "stage": "GS", "score": "", "date": "2025-06-25T00:00:00+00:00"},
        ],
        "upcoming": [],
    }
    _, dispatch = build_tools(redis_client=None)
    with patch("app.agent.tools.team.get_team_data", new=AsyncMock(return_value=fake_team)):
        out = await dispatch["count_team_matches"](id="624")
    assert out == {"played": 4, "won": 1, "lost": 1, "other": 2}
    assert out["played"] == out["won"] + out["lost"] + out["other"]


@pytest.mark.asyncio
async def test_get_events_tool_is_module_level_and_patchable():
    _, dispatch = build_tools(redis_client=object())
    with patch("app.agent.tools._get_events", new=AsyncMock(return_value=[{"id": "1"}])) as m:
        out = await dispatch["get_events"](pages=2)
    assert out == [{"id": "1"}]
    assert m.await_count == 1
