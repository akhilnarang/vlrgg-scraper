import asyncio
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.constants import TEST_MATCH_ID, MatchStatus, Platform
from app.cron import legacy_fcm, live_push, worker
from app.db.models import LiveActivityStart
from app.exceptions import ScrapingError
from app.schemas.matches import Favorites


def _live_detail(status: str, series: tuple[int, int]):
    from app.schemas.matches import Event, MatchData, MatchVideos, MatchWithDetails, Team, TeamWithImage

    return MatchWithDetails(
        teams=[
            TeamWithImage(id="1", name="Alpha", tag="ALP", score=series[0], img="https://cdn.vlr.gg/a.png"),
            TeamWithImage(id="2", name="Beta", tag="BET", score=series[1], img="https://cdn.vlr.gg/b.png"),
        ],
        bans=[],
        event=Event(id="99", img="https://cdn.vlr.gg/e.png", series="Series", stage="Stage", status=status),
        videos=MatchVideos(streams=[], vods=[]),
        map_count=1,
        data=[
            MatchData(
                map="Ascent",
                teams=[Team(name="Alpha", score=13), Team(name="Beta", score=9)],
                members=[],
                rounds=[],
            )
        ],
        previous_encounters=[],
    )


@pytest.mark.asyncio
async def test_arq_worker_recovers_after_redis_disconnect():
    replacement_started = asyncio.Event()

    async def run_replacement() -> None:
        replacement_started.set()
        await asyncio.Event().wait()

    failed_worker = SimpleNamespace(
        async_run=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        close=AsyncMock(),
    )
    replacement_worker = SimpleNamespace(
        async_run=AsyncMock(side_effect=run_replacement),
        close=AsyncMock(),
    )

    with (
        patch("app.cron.worker.create_worker", side_effect=[failed_worker, replacement_worker]),
        patch("app.cron.worker._ARQ_RESTART_DELAY", 0),
    ):
        arq_worker = worker.ArqWorker()
        await arq_worker.start()
        await asyncio.wait_for(replacement_started.wait(), timeout=1)
        await arq_worker.stop()


@pytest.mark.asyncio
async def test_fcm_cron_sends_valid_matches_and_reports_failures():
    current_time = datetime.now(tz=ZoneInfo(legacy_fcm.settings.TIMEZONE))
    valid_match = SimpleNamespace(
        id="123",
        status=MatchStatus.UPCOMING,
        time=current_time + timedelta(minutes=10),
        team1=SimpleNamespace(name="Team A"),
        team2=SimpleNamespace(name="Team B"),
    )
    invalid_match = SimpleNamespace(
        id="456",
        status=MatchStatus.UPCOMING,
        time=current_time + timedelta(minutes=12),
        team1=SimpleNamespace(name="Team C"),
        team2=SimpleNamespace(name="Team D"),
    )
    match_details = SimpleNamespace(
        teams=[SimpleNamespace(id="1"), SimpleNamespace(id="2")],
        event=SimpleNamespace(id="99"),
        videos=SimpleNamespace(streams=[]),
    )
    error = ValueError("invalid match details")
    firebase_app = object()

    with (
        patch("app.cron.legacy_fcm.matches.get_upcoming_matches", AsyncMock(return_value=[valid_match, invalid_match])),
        patch("app.cron.legacy_fcm.matches.match_by_id", AsyncMock(side_effect=[match_details, error])),
        patch("app.cron.legacy_fcm.capture_exception") as capture_exception,
        patch("app.cron.legacy_fcm.get_app", return_value=firebase_app),
        patch("app.cron.legacy_fcm.messaging.send_each_async", AsyncMock()) as send_each_async,
    ):
        await legacy_fcm.fcm_notification_cron({"redis": AsyncMock()})

    capture_exception.assert_called_once_with(error)
    send_each_async.assert_awaited_once()
    await_args = send_each_async.await_args
    assert await_args is not None
    messages = await_args.kwargs["messages"]
    assert len(messages) == 1
    assert messages[0].data["title"] == "Team A vs Team B"
    assert messages[0].data["match_id"] == "123"
    # Released apps subscribe to these legacy topics; live scores must never be sent to them.
    assert "'match-123' in topics" in messages[0].condition
    assert await_args.kwargs["app"] is firebase_app


@pytest.mark.asyncio
async def test_live_push_cron_starts_updates_and_ends_match(monkeypatch, tmp_path):
    import httpx2
    from firebase_admin import messaging

    from app.core import connections
    from app.db.engine import create_engine
    from app.db.migrations import upgrade_to_head
    from app.services import apns as apns_service
    from app.services.subscription_store import SubscriptionStore

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    await upgrade_to_head(database_url)
    engine = create_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client_id = "11111111-1111-4111-8111-111111111111"
    async with sessions.begin() as session:
        store = SubscriptionStore(session)
        await store.register_token(client_id, "aabb")
        await store.replace_favorites(client_id, Favorites(matches=["123"]))
        android_id = "22222222-2222-4222-8222-222222222222"
        await store.register_token(android_id, "fcm-token:APA91b", Platform.ANDROID)
        await store.replace_favorites(android_id, Favorites(matches=["123", TEST_MATCH_ID]))
        await store.save_match("456", None, None)  # an Android-only row whose page is deleted

    listed = SimpleNamespace(id="123", status=MatchStatus.LIVE)
    # After cycle 1, match 123 leaves the live listing; its stored row keeps it in the work set.
    # Its page then keeps failing and it ends with the last score on the third consecutive error
    # response. A timeout mid-run (VLR blackholing our network) is not a verdict on the match, so
    # it neither ends it nor counts. Match 456 returns 404 and ends at once. The synthetic match
    # below covers the regular final-page path.
    fetches = {
        "123": [
            _live_detail("upcoming", (1, 0)),
            ScrapingError(upstream_status=502),
            httpx2.ConnectTimeout("timed out"),
            ScrapingError(upstream_status=503),
            ScrapingError(upstream_status=500),
        ],
        "456": [ScrapingError(upstream_status=404)],
    }

    async def fetch(match_id, redis_client):
        outcome = fetches[match_id].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(live_push.matches, "get_upcoming_matches", AsyncMock(side_effect=[[listed], [], [], [], []]))
    match_by_id_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(live_push.matches, "match_by_id", match_by_id_mock)
    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", "configured")

    requests = []
    started_before_send = []
    blocked_during_channel_create = []

    async def handler(request):
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/channels"):
            # API writes must not wait on the cron's SQLite write lock during a provider call.
            try:
                with closing(sqlite3.connect(tmp_path / "db.sqlite3", timeout=0.1, isolation_level=None)) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.rollback()
                blocked_during_channel_create.append(False)
            except sqlite3.OperationalError:
                blocked_during_channel_create.append(True)
            return httpx2.Response(201, headers={"apns-channel-id": "channel-123"})
        if "/3/device/" in request.url.path:
            match_id = json.loads(request.content)["aps"]["attributes"]["match_id"]
            async with sessions() as session:
                started = await session.scalar(
                    select(LiveActivityStart.id).where(
                        LiveActivityStart.client_id == client_id,
                        LiveActivityStart.match_id == match_id,
                    )
                )
            # Recorded, not asserted here: the cron logs and skips exceptions raised during a send.
            started_before_send.append(started is not None)
        return httpx2.Response(200)

    credentials = apns_service.APNsCredentials(
        environment="sandbox",
        team_id="team",
        key_id="key",
        bundle_id="com.example.app",
        private_key_path=str(tmp_path / "unused.p8"),
    )
    apns = apns_service.APNsClient(credentials, transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(apns, "_jwt", lambda: "provider-token")
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(connections, "apns_client", apns)
    firebase_app = object()
    monkeypatch.setattr(live_push.fcm, "get_app", lambda: firebase_app)

    fcm_calls = []

    async def send_each_async(*, messages, dry_run, app):
        fcm_calls.append(messages)
        return messaging.BatchResponse(
            [messaging.SendResponse({"name": f"projects/x/messages/{index}"}, None) for index in range(len(messages))]
        )

    monkeypatch.setattr("app.services.fcm.messaging.send_each_async", send_each_async)

    ticks = {}

    def set_tick(key, value, nx=False):
        if nx and key in ticks:
            return None
        ticks[key] = int(value)
        return True

    def increment(key):
        ticks[key] = ticks.get(key, 0) + 1
        return ticks[key]

    redis = AsyncMock()
    redis.get.return_value = None
    redis.set.side_effect = set_tick
    redis.exists.side_effect = lambda key: key in ticks
    redis.delete.side_effect = lambda key: ticks.pop(key, None)
    redis.incr.side_effect = increment

    async def stored_match_ids():
        async with sessions() as session:
            return sorted(await SubscriptionStore(session).list_match_ids())

    try:
        await live_push.live_push_cron({"redis": redis})
        async with sessions() as session:
            row = await SubscriptionStore(session).get_match("123")
        assert row is not None and row.channel_id == "channel-123"
        assert await stored_match_ids() == ["123"]
        assert any("/3/device/aabb" in request.url.path for request in requests)
        assert fcm_calls and "'live-match-123' in topics" in fcm_calls[0][0].condition

        for failures in (1, 1, 2):
            await live_push.live_push_cron({"redis": redis})
            assert await stored_match_ids() == ["123"]
            assert ticks["vlrgg:push:fetch_failures:123"] == failures
        await live_push.live_push_cron({"redis": redis})
        assert await stored_match_ids() == []
        assert not [key for key in ticks if key.startswith("vlrgg:push:fetch_failures:")]
        assert match_by_id_mock.await_count == 6
        end_payloads = [
            json.loads(request.content)["aps"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
        ]
        assert [aps["event"] for aps in end_payloads] == ["end"]
        # The final state is rebuilt from the stored one, so the tags clients show must survive it.
        end_teams = end_payloads[0]["content-state"]["teams"]
        assert [(team["tag"], team["score"]) for team in end_teams] == [("ALP", 1), ("BET", 0)]
        assert [team["tag"] for team in json.loads(fcm_calls[1][0].data["state"])["teams"]] == ["ALP", "BET"]
        assert any(request.method == "DELETE" for request in requests)
        assert len(fcm_calls) == 2
        assert json.loads(fcm_calls[1][0].data["state"])["terminal"] is True

        from fastapi import FastAPI

        from app import constants
        from app.api import deps
        from app.api.v1.endpoints.live_updates import router

        async with sessions.begin() as session:
            await SubscriptionStore(session).replace_favorites(client_id, Favorites(matches=[constants.TEST_MATCH_ID]))
        monkeypatch.setattr(live_push.matches, "get_upcoming_matches", AsyncMock(return_value=[]))
        app = FastAPI()
        app.include_router(router, prefix="/api/v1/live-updates")
        app.dependency_overrides[deps.get_redis_client] = lambda: redis
        monkeypatch.setattr(deps.settings, "API_KEYS", {"test": "secret"})
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as api:
            response = await api.post("/api/v1/live-updates/test-match", headers={"Authorization": "Bearer secret"})
            duplicate = await api.post("/api/v1/live-updates/test-match", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 204
        assert duplicate.status_code == 409
        assert ticks[constants.TEST_TICK_KEY] == 0

        for _ in range(6):
            await live_push.live_push_cron({"redis": redis})
        assert constants.TEST_TICK_KEY not in ticks
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as api:
            restarted = await api.post("/api/v1/live-updates/test-match", headers={"Authorization": "Bearer secret"})
        assert restarted.status_code == 204
        async with sessions() as session:
            store = SubscriptionStore(session)
            assert await store.get_match(constants.TEST_MATCH_ID) is None
            assert (await store.get_favorites(client_id)).matches == [constants.TEST_MATCH_ID]
        assert sum("/3/device/aabb" in request.url.path for request in requests) == 2
        # Android FCM tokens are stored for later use but must never be sent to APNs.
        assert all(r.url.path.endswith("/aabb") for r in requests if "/3/device/" in r.url.path)
        assert started_before_send == [True, True]
        # Released apps render anything on the legacy topics, so live scores use only `live-*` topics.
        live_topics = re.findall(r"'([^']+)' in topics", " ".join(m.condition for call in fcm_calls for m in call))
        assert live_topics and all(topic.startswith("live-") for topic in live_topics)
        assert blocked_during_channel_create == [False, False]
        # A start is sent once and never retried, so APNs must hold it for a device that is briefly unreachable,
        # and channels keep the latest broadcast for devices that were offline when it was sent.
        starts = [r for r in requests if "/3/device/" in r.url.path]
        assert starts and all(int(r.headers["apns-expiration"]) > time.time() + 300 for r in starts)
        channels = [r for r in requests if r.method == "POST" and r.url.path.endswith("/channels")]
        assert [json.loads(r.content)["message-storage-policy"] for r in channels] == [1, 1]
        test_events = [
            json.loads(request.content)["aps"]["event"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
        ]
        assert test_events == ["end", "update", "update", "update", "update", "end"]
        assert f"'live-match-{constants.TEST_MATCH_ID}' in topics" in fcm_calls[-1][0].condition
        last_fcm_state = json.loads(fcm_calls[-1][0].data["state"])
        assert last_fcm_state["terminal"] is True
        assert last_fcm_state["total_maps"] == 3
        assert last_fcm_state["current_map"]["number"] == 1
        assert match_by_id_mock.await_count == 6  # synthetic observations never hit VLR

        # Fetched match pages store their teams with tags; the synthetic test match stays out of the store.
        from app import schemas
        from app.cron import jobs
        from app.db.models import Team

        async def stored_teams():
            async with sessions() as session:
                return {team.id: (team.name, team.tag, team.rank) for team in await session.scalars(select(Team))}

        assert await stored_teams() == {"1": ("Alpha", "ALP", None), "2": ("Beta", "BET", None)}
        # A rankings run adds the rank and keeps the tag it doesn't carry.
        ranked = schemas.Ranking(
            region="Europe",
            teams=[
                schemas.TeamRanking(
                    name="Alpha", id=1, logo="https://cdn.vlr.gg/a.png", rank=3, points=900, country="France"
                )
            ],
        )
        monkeypatch.setattr(jobs.rankings, "ranking_list", AsyncMock(return_value=[ranked]))
        await jobs.rankings_cron({"redis": AsyncMock()})
        assert (await stored_teams())["1"] == ("Alpha", "ALP", 3)
        # VLR shows no tag before a match's first map; that must not erase the stored one.
        from app.services import scrape_store

        async with sessions.begin() as session:
            await scrape_store.upsert_team(session, "1", name="Alpha", tag=None)
        assert (await stored_teams())["1"] == ("Alpha", "ALP", 3)

        # With nothing live or starting soon in the cached list, idle minutes must not fetch VLR's listing.
        from app import schemas

        monkeypatch.setattr("app.cache.cache.settings.ENABLE_CACHE", True)
        listing = AsyncMock(return_value=[])
        monkeypatch.setattr(live_push.matches, "get_upcoming_matches", listing)
        now = datetime.now(ZoneInfo("UTC"))
        cached = [
            schemas.Match(
                id=match_id,
                team1=schemas.MatchTeam(name="A"),
                team2=schemas.MatchTeam(name="B"),
                status=status,
                time=now + offset,
                event="Event",
                series="Series",
            )
            for match_id, status, offset in (
                ("1", MatchStatus.COMPLETED, timedelta(hours=-1)),
                ("2", MatchStatus.UPCOMING, timedelta(hours=2)),
            )
        ]
        redis.get.return_value = schemas.MatchListAdapter.dump_json(cached)
        await live_push.live_push_cron({"redis": redis})
        listing.assert_not_awaited()
        cached[1].time = now + timedelta(minutes=5)
        redis.get.return_value = schemas.MatchListAdapter.dump_json(cached)
        await live_push.live_push_cron({"redis": redis})
        listing.assert_awaited_once()
    finally:
        await apns.aclose()
        await engine.dispose()
