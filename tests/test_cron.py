import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import cron, schemas
from app.constants import LIVE_TEST_MATCH_ID, MatchStatus
from app.services import live


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
        patch("app.cron.create_worker", side_effect=[failed_worker, replacement_worker]),
        patch("app.cron._ARQ_RESTART_DELAY", 0),
    ):
        arq_worker = cron.ArqWorker()
        await arq_worker.start()
        await asyncio.wait_for(replacement_started.wait(), timeout=1)
        await arq_worker.stop()


@pytest.mark.asyncio
async def test_fcm_cron_sends_valid_matches_and_reports_failures():
    current_time = datetime.now(tz=ZoneInfo(cron.settings.TIMEZONE))
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
        patch("app.cron.matches.get_upcoming_matches", AsyncMock(return_value=[valid_match, invalid_match])),
        patch("app.cron.matches.match_by_id", AsyncMock(side_effect=[match_details, error])),
        patch("app.cron.capture_exception") as capture_exception,
        patch("app.cron._get_fcm_app", return_value=firebase_app),
        patch("app.cron.messaging.send_each_async", AsyncMock()) as send_each_async,
    ):
        await cron.fcm_notification_cron({"redis": AsyncMock()})

    capture_exception.assert_called_once_with(error)
    send_each_async.assert_awaited_once()
    await_args = send_each_async.await_args
    assert await_args is not None
    messages = await_args.kwargs["messages"]
    assert len(messages) == 1
    assert messages[0].data["title"] == "Team A vs Team B"
    assert messages[0].data["match_id"] == "123"
    assert await_args.kwargs["app"] is firebase_app


def _match_detail():
    from app.schemas.matches import Event, MatchVideos, MatchWithDetails

    return MatchWithDetails(
        teams=[],
        bans=[],
        event=Event(id="2283", img="https://cdn.vlr.gg/e.png", series="Series", stage="Group", status="completed"),
        videos=MatchVideos(streams=[], vods=[]),
        map_count=0,
        data=[],
        previous_encounters=[],
    )


@pytest.mark.asyncio
async def test_live_cron_fetches_each_watched_match_once(monkeypatch, live_redis):
    """Many subscribers to one id still produce one detail fetch and one shared snapshot."""
    monkeypatch.setattr(cron.settings, "ENABLE_LIVE_MATCHES", True)
    redis = live_redis()
    await live.register(redis, "stream-a", ["123"])
    await live.register(redis, "stream-b", ["123"])

    with patch("app.cron.matches.match_by_id", AsyncMock(return_value=_match_detail())) as match_by_id:
        await cron.live_matches_cron({"redis": redis})
        assert match_by_id.await_count == 1
        await_args = match_by_id.await_args
        assert await_args is not None and await_args.args[0] == "123"
        # Subscriber connections read the same shared snapshot.
        shared = (await live.read_snapshots(redis, ["123"]))["123"]
        assert shared is not None and shared.match_id == "123"

        # After the last release the next cycle is idle and fetches nothing.
        await live.release(redis, "stream-a", ["123"])
        await live.release(redis, "stream-b", ["123"])
        await cron.live_matches_cron({"redis": redis})
        assert match_by_id.await_count == 1


@pytest.mark.asyncio
async def test_live_cron_keeps_last_good_snapshot_on_failure(monkeypatch, live_redis):
    monkeypatch.setattr(cron.settings, "ENABLE_LIVE_MATCHES", True)
    redis = live_redis()
    await live.register(redis, "stream-a", ["123"])
    previous = await live.store_snapshot(redis, "123", _match_detail())

    with (
        patch("app.cron.matches.match_by_id", AsyncMock(side_effect=RuntimeError("vlr unavailable"))),
        patch("app.cron.capture_exception") as capture_exception,
    ):
        await cron.live_matches_cron({"redis": redis})

    current = schemas.LiveSnapshot.model_validate_json(redis.values[live.snapshot_key("123")])
    assert current.version == previous.version
    capture_exception.assert_called_once()


@pytest.mark.asyncio
async def test_live_test_match_updates_without_upstream_and_resets(monkeypatch, live_redis):
    """The reserved id walks fixed phases without VLR, projects compact scores, and restarts after disconnect."""
    monkeypatch.setattr(cron.settings, "ENABLE_LIVE_MATCHES", True)
    redis = live_redis()
    match_id = LIVE_TEST_MATCH_ID
    await live.register(redis, "stream-a", [match_id])

    with patch("app.cron.matches.match_by_id", AsyncMock()) as match_by_id:
        projected = []
        for _ in range(4):
            await cron.live_matches_cron({"redis": redis})
            snapshot = (await live.read_snapshots(redis, [match_id]))[match_id]
            assert snapshot is not None
            projected.append(live.project_live_event(snapshot))

    match_by_id.assert_not_awaited()
    assert [(event.terminal, event.current_map.name, event.current_map.scores) for event in projected] == [
        (False, "Haven", [4, 2]),
        (False, "Haven", [9, 7]),
        (False, "Ascent", [5, 4]),
        (True, "Ascent", [13, 10]),
    ]
    assert [[team.score for team in event.teams] for event in projected] == [
        [0, 0],
        [0, 0],
        [1, 0],
        [2, 0],
    ]

    await live.release(redis, "stream-a", [match_id])
    assert live.snapshot_key(match_id) not in redis.values

    await live.register(redis, "stream-b", [match_id])
    await cron.live_matches_cron({"redis": redis})
    restarted = (await live.read_snapshots(redis, [match_id]))[match_id]
    assert restarted is not None and restarted.data.data[0].teams[0].score == 4
