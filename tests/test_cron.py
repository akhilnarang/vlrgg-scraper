from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import cron
from app.constants import MatchStatus


@pytest.mark.asyncio
async def test_fcm_notification_cron_uses_async_send_with_reused_app():
    current_time = datetime.now(tz=ZoneInfo(cron.settings.TIMEZONE))
    upcoming_match = SimpleNamespace(
        id="123",
        status=MatchStatus.UPCOMING,
        time=current_time + timedelta(minutes=10),
        team1=SimpleNamespace(name="Team A"),
        team2=SimpleNamespace(name="Team B"),
    )
    match_details = SimpleNamespace(
        teams=[SimpleNamespace(id="1"), SimpleNamespace(id="2")],
        event=SimpleNamespace(id="99"),
        videos=SimpleNamespace(streams=[]),
    )
    app = object()

    with (
        patch("app.cron.matches.get_upcoming_matches", AsyncMock(return_value=[upcoming_match])),
        patch("app.cron.matches.match_by_id", AsyncMock(return_value=match_details)),
        patch("app.cron._get_fcm_app", return_value=app) as get_fcm_app,
        patch("app.cron.messaging.send_each_async", AsyncMock()) as send_each_async,
    ):
        await cron.fcm_notification_cron({"redis": AsyncMock()})

    get_fcm_app.assert_called_once_with()
    send_each_async.assert_awaited_once()
    _, kwargs = send_each_async.await_args
    assert kwargs["app"] is app
    assert kwargs["dry_run"] is False
    assert len(kwargs["messages"]) == 1
    assert kwargs["messages"][0].data["title"] == "Team A vs Team B"
    assert kwargs["messages"][0].data["match_id"] == "123"


@pytest.mark.asyncio
async def test_close_fcm_app_uses_worker_safe_cleanup():
    app = object()

    with (
        patch("app.cron.get_app", return_value=app),
        patch("app.cron.asyncio.to_thread", AsyncMock()) as to_thread,
    ):
        await cron._close_fcm_app()

    to_thread.assert_awaited_once_with(cron.delete_app, app)


def test_get_fcm_app_returns_existing_app_without_initializing():
    app = object()

    with (
        patch("app.cron.get_app", return_value=app) as get_app,
        patch("app.cron.initialize_app") as initialize_app,
    ):
        result = cron._get_fcm_app()

    assert result is app
    get_app.assert_called_once_with("vlrgg-fcm")
    initialize_app.assert_not_called()


def test_get_fcm_app_returns_existing_app_if_concurrent_init_wins():
    app = object()
    credential = object()
    get_app_calls = 0

    def get_app_side_effect(*_args, **_kwargs):
        nonlocal get_app_calls
        get_app_calls += 1
        if get_app_calls == 1:
            raise ValueError
        return app

    with (
        patch("app.cron.get_app", side_effect=get_app_side_effect) as get_app,
        patch("app.cron.credentials.Certificate", return_value=credential),
        patch("app.cron.initialize_app", side_effect=ValueError()) as initialize_app,
    ):
        result = cron._get_fcm_app()

    assert result is app
    initialize_app.assert_called_once_with(
        name="vlrgg-fcm",
        credential=credential,
    )
    assert get_app.call_count == 2


def test_get_fcm_app_initializes_when_missing():
    app = object()
    credential = object()

    with (
        patch("app.cron.get_app", side_effect=[ValueError(), ValueError()]) as get_app,
        patch("app.cron.credentials.Certificate", return_value=credential),
        patch("app.cron.initialize_app", return_value=app) as initialize_app,
    ):
        result = cron._get_fcm_app()

    assert result is app
    get_app.assert_called_once_with("vlrgg-fcm")
    initialize_app.assert_called_once_with(
        name="vlrgg-fcm",
        credential=credential,
    )


def test_get_fcm_app_reraises_original_init_error_when_no_app_exists():
    credential = object()
    init_error = ValueError("invalid service account")

    with (
        patch("app.cron.get_app", side_effect=[ValueError(), ValueError()]),
        patch("app.cron.credentials.Certificate", return_value=credential),
        patch("app.cron.initialize_app", side_effect=init_error),
    ):
        with pytest.raises(ValueError, match="invalid service account"):
            cron._get_fcm_app()


@pytest.mark.asyncio
async def test_close_fcm_app_skips_missing_app():
    with (
        patch("app.cron.get_app", side_effect=ValueError),
        patch("app.cron.asyncio.to_thread", AsyncMock()) as to_thread,
    ):
        await cron._close_fcm_app()

    to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_fcm_app_logs_and_continues_on_cleanup_failure():
    app = object()

    with (
        patch("app.cron.get_app", return_value=app),
        patch("app.cron.asyncio.to_thread", AsyncMock(side_effect=RuntimeError("boom"))) as to_thread,
        patch("app.cron.logging.exception") as log_exception,
    ):
        await cron._close_fcm_app()

    to_thread.assert_awaited_once_with(cron.delete_app, app)
    log_exception.assert_called_once_with("Failed to delete Firebase app during shutdown: %s", "vlrgg-fcm")
