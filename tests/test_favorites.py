from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import schemas
from app.api import deps
from app.api.v1.endpoints.favorites import router
from app.core import connections
from app.cron import favorite_players
from app.db.engine import create_engine
from app.db.migrations import upgrade_to_head
from app.schemas.matches import Favorites
from app.schemas.player import PlayerTeam
from app.services.subscription_store import SubscriptionStore

CLIENT_ID = "11111111-1111-4111-8111-111111111111"
START = datetime.now(UTC).replace(microsecond=0)


def _match(match_id, team1, team2, status="upcoming", hours=0.0, event_id="999"):
    return schemas.Match(
        id=match_id,
        team1=schemas.MatchTeam(name=f"T{team1}", id=team1),
        team2=schemas.MatchTeam(name=f"T{team2}", id=team2),
        status=status,
        time=START + timedelta(hours=hours),
        event="Event",
        series="Series",
        event_id=event_id,
    )


@pytest.mark.asyncio
async def test_favorite_matches_follow_teams_players_events_and_matches(monkeypatch, tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    await upgrade_to_head(database_url)
    engine = create_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    async with sessions.begin() as session:
        await SubscriptionStore(session).add_favorites(
            CLIENT_ID, Favorites(teams=["624"], events=["2766"], players=["9"], matches=["800"])
        )

    listed = [
        _match("L1", "624", "1", status="live"),
        *(_match(f"U{n}", "624", "2", hours=n) for n in range(1, 7)),  # six upcoming: only five survive the cap
        _match("C1", "624", "3", status="completed", hours=-5),
        _match("C2", "5", "6", status="completed", hours=-2, event_id="2766"),
        _match("C3", "624", "4", status="completed", hours=-30),  # outside the 24 h results window
        _match("E1", "5", "6", hours=2.5, event_id="2766"),
        _match("P1", "77", "8", hours=30),  # player 9 plays for team 77
        _match("800", "10", "11", hours=40),
        _match("X1", "12", "13", hours=1.5),  # unrelated
    ]
    redis = AsyncMock()
    redis.get.return_value = schemas.MatchListAdapter.dump_json(listed)
    monkeypatch.setattr("app.cache.cache.settings.ENABLE_CACHE", True)
    scrape = AsyncMock(
        return_value=schemas.Player(
            name="Player Nine",
            alias="nine",
            country="India",
            img="https://owcdn.net/img/nine.png",
            agents=[],
            current_team=PlayerTeam(id="77", name="T77", img="https://owcdn.net/img/77.png"),
        )
    )
    monkeypatch.setattr(favorite_players.player, "get_player_data", scrape)
    # The players cron resolves player 9 to team 77; requests themselves never fetch from VLR.
    await favorite_players.favorite_players_cron({})
    await favorite_players.favorite_players_cron({})  # already fresh: not fetched again

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/favorites")
    app.dependency_overrides[deps.get_redis_client] = lambda: redis
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as api:
            first = await api.get(f"/api/v1/favorites/{CLIENT_ID}/matches")
            second = await api.get(f"/api/v1/favorites/{CLIENT_ID}/matches")
            with_results = await api.get(f"/api/v1/favorites/{CLIENT_ID}/matches", params={"include_results": "true"})
            unknown = await api.get("/api/v1/favorites/22222222-2222-4222-8222-222222222222/matches")
            redis.get.return_value = None
            cold_cache = await api.get(f"/api/v1/favorites/{CLIENT_ID}/matches")
    finally:
        await engine.dispose()

    assert first.status_code == 200
    # Live first, then by start time; completed and unrelated matches are left out.
    expected = ["L1", "U1", "U2", "E1", "U3", "U4", "P1", "800"]
    assert [match["id"] for match in first.json()] == expected
    assert first.headers["cache-control"] == "no-store"
    assert [match["id"] for match in second.json()] == expected
    scrape.assert_awaited_once_with("9")
    # Opting in appends the last 24 hours of favorite results, newest first.
    assert [match["id"] for match in with_results.json()] == [*expected, "C2", "C1"]
    assert unknown.status_code == 404
    # An empty match list cache is a 503, never a per-request VLR fetch.
    assert cold_cache.status_code == 503
