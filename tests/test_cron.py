import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import cron
from app.constants import MatchStatus


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
        patch("app.cron.create_worker", side_effect=[failed_worker, replacement_worker]) as create_worker,
        patch("app.cron._ARQ_RESTART_INITIAL_DELAY", 0),
    ):
        arq_worker = cron.ArqWorker()
        await arq_worker.start()
        await replacement_started.wait()
        await arq_worker.stop()

    assert create_worker.call_count == 2
    failed_worker.close.assert_awaited_once()
    replacement_worker.close.assert_awaited_once()


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
