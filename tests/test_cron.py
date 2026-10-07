import asyncio
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import DEFAULT, AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import constants
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
                number=1,
                map="Ascent",
                teams=[Team(name="Alpha", score=13), Team(name="Beta", score=9)],
                members=[],
                rounds=[],
            )
        ],
        previous_encounters=[],
    )


def test_video_score_targets_its_original_game_number():
    from app.schemas.matches import MatchData, PushMapRounds, Round, Team, VideoPause, VideoScore
    from app.services import push

    detail = _live_detail("live", (0, 0))
    detail.total_maps = 4
    detail.data.append(
        MatchData(
            number=4,
            map="Lotus",
            teams=[Team(name="Alpha", score=0), Team(name="Beta", score=0)],
            members=[],
            rounds=[
                Round(round_number=1, round_score="1-0", winner="team1", side="attack", win_type="Elimination"),
                Round(round_number=2, round_score="2-0", winner="team1", side="attack", win_type="Elimination"),
            ],
        )
    )
    video = VideoScore.model_validate(
        {
            "status": "ok",
            "observed_at": int(time.time()),
            "map_number": 4,
            "teams": [
                {"code": "ALP", "name": "Alpha", "score": 8},
                {"code": "BET", "name": "Beta", "score": 4},
            ],
        }
    )

    scores = push.video_team_scores(detail, video)
    assert scores == {"alpha": 8, "beta": 4}
    push.raise_map_scores(detail, video.map_number, scores)

    assert [[team.score for team in map_data.teams] for map_data in detail.data] == [
        [13, 9],
        [8, 4],
    ]
    state = push.project_state("123", detail)
    assert state is not None and state.current_map is not None
    assert (state.current_map.name, state.current_map.number, state.current_map.scores) == ("Lotus", 4, [8, 4])
    assert [team.name for team in state.teams] == ["Alpha", "Beta"]

    video.map_number = 3
    assert push.video_team_scores(detail, video) is None

    video.teams[0].side, video.teams[1].side = constants.TeamSide.RED, constants.TeamSide.BLUE
    push.order_teams_for_broadcast(detail, video)
    assert [team.name for team in detail.teams] == ["Alpha", "Beta"]

    video.map_number = 4
    push.order_teams_for_broadcast(detail, video)
    state = push.project_state("123", detail)
    assert state is not None and state.current_map is not None
    assert [team.name for team in state.teams] == ["Beta", "Alpha"]
    assert state.current_map.scores == [4, 8]
    assert state.map_round_winners == [
        PushMapRounds(map_number=1),
        PushMapRounds(map_number=2),
        PushMapRounds(map_number=3),
        PushMapRounds(map_number=4, winners=[1, 1]),
    ]

    detail.data[1].teams[0].score, detail.data[1].teams[1].score = 9, 13
    state = push.project_state("123", detail)
    assert state is not None
    assert state.map_winners == ["1", None, None, "2"]

    video.pause = VideoPause(kind="tech_pause", reason="GEAR", since=120)
    paused = push.project_state("123", detail, video)
    assert paused is not None and paused.pause is not None
    assert paused.pause.model_dump(mode="json") == {"kind": "tech_pause", "reason": "GEAR"}
    assert paused.semantic() != push.project_state("123", detail).semantic()
    video.map_number = 3
    assert push.project_state("123", detail, video).pause is None
    video.map_number = 5
    assert push.project_state("123", detail, video).pause is not None
    video.map_number = 4
    video.observed_at = int(time.time()) - constants.VIDEO_STALE_SECONDS - 1
    assert push.project_state("123", detail, video).pause is None


@pytest.mark.asyncio
async def test_apns_indexes_full_history_without_changing_android_state(monkeypatch, tmp_path):
    """APNs adds indexed strings while existing iOS and Android clients retain their original state fields."""
    import httpx2

    from app.schemas.matches import Round
    from app.services import apns as apns_service
    from app.services import fcm, push

    detail = _live_detail("live", (1, 0))
    detail.total_maps = 5
    detail.data[0].rounds = [
        Round(round_number=number, round_score="", winner=winner, side="attack", win_type="Elimination")
        for number, winner in [(1, "team1"), (2, "team2"), (3, "team1")]
    ]
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content)["aps"])
        return httpx2.Response(200)

    client = apns_service.APNsClient(
        apns_service.APNsCredentials(
            team_id="test", key_id="test", bundle_id="com.example.test", private_key_path=str(tmp_path / "unused.p8")
        ),
        transport=httpx2.MockTransport(handler),
    )
    monkeypatch.setattr(client, "_jwt", lambda: "provider-token")
    async with client.client:
        state = push.project_state("123", detail)
        assert state is not None
        await client.send_start("aabb", "channel-123", state)
        message = fcm.build_direct_message("tok:123", state)
        assert message.data is not None
        android = json.loads(message.data["state"])
        assert "team_0" not in android and "team_1" not in android
        assert android["map_winners"] == ["1", None, None, None, None]
        assert android["map_round_winners"] == [
            {"map_number": 1, "winners": [0, 1, 0]},
            *[{"map_number": number, "winners": []} for number in range(2, 6)],
        ]

        detail.teams.reverse()
        state = push.project_state("123", detail)
        assert state is not None
        await client.publish("channel-123", state, terminal=False)

        detail.data[0].rounds[1].winner = "unknown"
        detail.teams[0].id = None
        state = push.project_state("123", detail)
        assert state is not None
        await client.publish("channel-123", state, terminal=False)

        await client.publish("channel-123", state, terminal=True)

    initial, swapped, unknown, final = [item["content-state"] for item in payloads]
    assert [item["event"] for item in payloads] == ["start", "update", "update", "end"]
    assert {key: initial[key] for key in android} == android
    assert (initial["team_0"], initial["team_1"]) == ("1", "2")
    assert "map_winner_indexes" not in initial
    assert initial["map_rounds"] == ["010", "", "", "", ""]
    assert (swapped["team_0"], swapped["team_1"]) == ("2", "1")
    assert [team["id"] for team in swapped["teams"]] == ["2", "1"]
    assert swapped["map_winners"] == ["1", None, None, None, None]
    assert swapped["map_rounds"] == ["101", "", "", "", ""]
    assert (unknown["team_0"], unknown["team_1"]) == (None, "1")
    assert unknown["map_round_winners"][0]["winners"] == [1, None, 1]
    assert unknown["map_rounds"] == final["map_rounds"] == ["", "", "", "", ""]
    assert unknown["current_map"]["scores"] == final["current_map"]["scores"] == [9, 13]


@pytest.mark.asyncio
async def test_live_push_cron_applies_video_score_only_to_resolved_match(monkeypatch):
    from app.core import connections
    from app.schemas.matches import VideoScore
    from app.services import push

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def commit(self):
            pass

    class Store:
        def __init__(self, session):
            pass

        async def list_match_ids(self):
            return []

        async def get_match(self, match_id):
            return None

        async def save_match(self, *args):
            pass

        async def live_android_tokens(self, routing):
            return [f"token:{routing.match_id}"]

    details = {match_id: _live_detail("live", (0, 0)) for match_id in ("123", "456")}
    for detail in details.values():
        for team in detail.data[0].teams:
            team.score = 0
    video = VideoScore.model_validate(
        {
            "status": "ok",
            "observed_at": int(time.time()),
            "map_number": 1,
            "teams": [
                {"code": "ALP", "name": "Alpha", "score": 8},
                {"code": "BET", "name": "Beta", "score": 4},
            ],
            "pause": {"kind": "tech_pause", "reason": "GEAR", "since": 120},
        }
    )
    sent = []

    async def publish_direct(app, tokens, state):
        sent.append(state)
        return [f"message:{len(sent)}"]

    async def fetch_detail(client, match_id):
        return details[match_id]

    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", "configured")
    monkeypatch.setattr(connections, "subscription_sessions", Session)
    monkeypatch.setattr(live_push, "SubscriptionStore", Store)
    monkeypatch.setattr(push, "video_score", AsyncMock(return_value=video))
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_listed_live_ids", AsyncMock(return_value=set(details)))
    monkeypatch.setattr(live_push, "_fetch_detail", fetch_detail)
    monkeypatch.setattr(live_push, "_cache_details", AsyncMock())
    monkeypatch.setattr(
        push, "resolve_video_match", AsyncMock(return_value=("123", details["123"], {"alpha": 8, "beta": 4}))
    )
    monkeypatch.setattr(live_push.fcm, "get_app", lambda: object())
    monkeypatch.setattr(live_push.fcm, "publish_direct", publish_direct)
    mark_delivered = AsyncMock()
    monkeypatch.setattr(live_push, "_mark_delivered", mark_delivered)
    monkeypatch.setattr(live_push, "_store_teams", AsyncMock())
    redis = AsyncMock()

    await live_push.live_push_cron({"redis": redis})

    assert [(state.match_id, state.current_map.scores if state.current_map else None) for state in sent] == [
        ("123", [8, 4]),
        ("456", [0, 0]),
    ]
    assert sent[0].pause is not None and (sent[0].pause.kind, sent[0].pause.reason) == ("tech_pause", "GEAR")
    assert sent[1].pause is None
    mark_delivered.assert_awaited_once_with(redis, "123", video)

    video.map_number = 2
    await live_push.live_push_cron({"redis": redis})
    assert mark_delivered.await_count == 1


def test_video_scores_require_one_to_one_team_identity():
    """One broadcast entry must not stand in for both VLR teams; name/tag evidence must pair one-to-one."""
    from app.schemas.matches import Team, VideoScore
    from app.services import push

    detail = _live_detail("live", (0, 0))
    detail.data[0].teams = [Team(name="Alpha", score=8), Team(name="Beta", score=8)]
    crossed = VideoScore.model_validate(
        {
            "status": "ok",
            "observed_at": int(time.time()),
            "map_number": 1,
            "teams": [
                {"code": "BET", "name": "Alpha", "score": 8},
                {"code": "XYZ", "name": "Gamma", "score": 7},
            ],
        }
    )

    assert push.video_team_scores(detail, crossed) is None

    paired = VideoScore.model_validate(
        {
            "status": "ok",
            "observed_at": int(time.time()),
            "map_number": 1,
            "teams": [
                {"code": "XYZ", "name": "Alpha", "score": 8},
                {"code": "BET", "name": "Gamma", "score": 4},
            ],
        }
    )
    assert push.video_team_scores(detail, paired) == {"alpha": 8, "beta": 4}

    ambiguous = VideoScore.model_validate(
        {
            "status": "ok",
            "observed_at": int(time.time()),
            "map_number": 1,
            "teams": [
                {"code": "ALP", "name": "Alpha", "score": 8},
                {"code": "BET", "name": "Beta", "score": 4},
            ],
        }
    )
    detail.teams[0].tag, detail.teams[1].tag = "BET", "ALP"
    assert push.video_team_scores(detail, ambiguous) is None


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
    assert "'match-123' in topics" in messages[0].condition
    assert await_args.kwargs["app"] is firebase_app


@pytest.mark.asyncio
async def test_publish_direct_batches_past_the_firebase_limit(monkeypatch):
    """More than 500 followers must be sent in Firebase-sized batches, not one rejected call."""
    import warnings

    from firebase_admin import messaging

    from app.schemas.matches import CompactState, PushTeam
    from app.services import fcm

    state = CompactState(
        match_id="123",
        observed_at=int(time.time()),
        terminal=False,
        teams=[PushTeam(name="Alpha"), PushTeam(name="Beta")],
    )
    batches = []

    async def send_each_async(*, messages, dry_run, app):
        if len(messages) > 500:
            raise ValueError("messages must not contain more than 500 elements")
        batches.append(len(messages))
        return messaging.BatchResponse(
            [messaging.SendResponse({"name": f"projects/x/messages/{index}"}, None) for index in range(len(messages))]
        )

    monkeypatch.setattr(fcm.messaging, "send_each_async", send_each_async)
    tokens = [f"fcm-token:{index}" for index in range(501)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        message_ids = await fcm.publish_direct(object(), tokens, state)

    assert batches == [500, 1]
    assert len(message_ids) == 501


@pytest.mark.asyncio
async def test_direct_delivery_failures_propagate_to_the_delivered_guard(monkeypatch):
    """A failed send is not a delivery, so the video stays with VLR; unregistered tokens are still cleared."""
    from firebase_admin import exceptions, messaging

    from app.schemas.matches import CompactState, PushTeam
    from app.services import fcm

    state = CompactState(
        match_id="123",
        observed_at=int(time.time()),
        terminal=False,
        teams=[PushTeam(name="Alpha"), PushTeam(name="Beta")],
    )
    cleared = []

    async def clear_token(token):
        cleared.append(token)

    async def force(*, messages, dry_run, app):
        return messaging.BatchResponse(
            [messaging.SendResponse(None, exceptions.UnavailableError("server busy")) for _ in messages]
        )

    monkeypatch.setattr(fcm.push, "clear_rejected_token", clear_token)
    monkeypatch.setattr(fcm.messaging, "send_each_async", force)
    with pytest.raises(fcm.FCMError):
        await fcm.publish_direct(object(), ["tok:1"], state)

    assert await live_push._send_fcm(["tok:1"], object(), state) is False

    async def unregistered(*, messages, dry_run, app):
        return messaging.BatchResponse(
            [messaging.SendResponse(None, messaging.UnregisteredError("registration token expired")) for _ in messages]
        )

    monkeypatch.setattr(fcm.messaging, "send_each_async", unregistered)
    assert await live_push._send_fcm(["tok:2"], object(), state) is True
    assert cleared == ["tok:2"]


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
        await store.add_favorites(client_id, Favorites(matches=["123"]))
        android_id = "22222222-2222-4222-8222-222222222222"
        await store.register_token(android_id, "fcm-token:APA91b", Platform.ANDROID)
        await store.add_favorites(android_id, Favorites(matches=["123", TEST_MATCH_ID]))
        await store.save_match("456", None, None)
        live_off_id = "33333333-3333-4333-8333-333333333333"
        await store.register_token(live_off_id, "ccdd", live_updates=False)
        await store.add_favorites(live_off_id, Favorites(matches=["123"]))

    listed = SimpleNamespace(id="123", status=MatchStatus.LIVE)
    fetches = {
        "123": [
            _live_detail("upcoming", (1, 0)),
            _live_detail("upcoming", (1, 0)),
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

    monkeypatch.setattr(live_push.matches, "get_upcoming_matches", AsyncMock(side_effect=[[listed]] * 4 + [[]] * 4))
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

    videos = {}

    def set_tick(key, value, nx=False, ex=None, get=False):
        if key.startswith(("vlrgg:push:video", constants.PUSH_DETAILS_KEY)):
            previous, videos[key] = videos.get(key), value
            return previous
        if nx and key in ticks:
            return None
        ticks[key] = int(value)
        return True

    def increment(key):
        ticks[key] = ticks.get(key, 0) + 1
        return ticks[key]

    redis = AsyncMock()
    redis.get.return_value = None
    redis.get.side_effect = lambda key: videos.get(key, DEFAULT)
    redis.set.side_effect = set_tick
    redis.exists.side_effect = lambda key: key in ticks
    redis.delete.side_effect = lambda key: ticks.pop(key, None)
    redis.incr.side_effect = increment

    async def stored_match_ids():
        async with sessions() as session:
            return sorted(await SubscriptionStore(session).list_match_ids())

    from fastapi import Depends, FastAPI

    from app.api import deps
    from app.api.v1.endpoints.video import router as video_router

    token_file = tmp_path / "video-token"
    token_file.write_text("tracker-token")
    monkeypatch.setattr(deps.settings, "VIDEO_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(live_push.cache, "get_client", lambda: redis)
    video_app = FastAPI()
    video_app.include_router(video_router, prefix="/api/v1/video", dependencies=[Depends(deps.verify_video_token)])
    video_app.dependency_overrides[deps.get_redis_client] = lambda: redis
    video = {
        "status": "ok",
        "observed_at": int(time.time()),
        "map_number": 1,
        "teams": [{"code": "ALP", "name": "Alpha Esports", "score": 12}, {"code": "XYZ", "name": "beta", "score": 9}],
    }

    async def put_video(token="tracker-token"):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=video_app), base_url="http://test") as api:
            return await api.put("/api/v1/video/score", json=video, headers={"X-Video-Token": token})

    def pushed_scores(call):
        return json.loads(call[0].data["state"])["current_map"]["scores"]

    try:
        await live_push.live_push_cron({"redis": redis})
        async with sessions() as session:
            row = await SubscriptionStore(session).get_match("123")
        assert row is not None and row.channel_id == "channel-123"
        assert await stored_match_ids() == ["123"]
        assert any("/3/device/aabb" in request.url.path for request in requests)
        assert not any("/3/device/ccdd" in request.url.path for request in requests)
        assert fcm_calls and [message.token for message in fcm_calls[0]] == ["fcm-token:APA91b"]
        assert fcm_calls[0][0].android.priority == "high"
        assert fcm_calls[0][0].android.collapse_key is None
        vlr_fetches = match_by_id_mock.await_count

        assert (await put_video(token="guess")).status_code == 401
        assert constants.VIDEO_SCORE_KEY not in videos and len(fcm_calls) == 1
        assert (await put_video()).status_code == 204
        assert pushed_scores(fcm_calls[1]) == [13, 9]
        assert match_by_id_mock.await_count == vlr_fetches
        assert (await put_video()).status_code == 204
        await live_push.live_push_cron({"redis": redis})
        assert len(fcm_calls) == 2 and match_by_id_mock.await_count == vlr_fetches
        silent = json.loads(videos[constants.VIDEO_SCORE_KEY])
        silent["observed_at"] -= constants.VIDEO_STALE_SECONDS + 1
        videos[constants.VIDEO_SCORE_KEY] = json.dumps(silent)
        await live_push.live_push_cron({"redis": redis})
        assert len(fcm_calls) == 3
        video["status"] = "error"
        video["teams"][0]["score"], video["teams"][1]["score"] = 13, 10
        assert (await put_video()).status_code == 204
        assert pushed_scores(fcm_calls[3]) == [13, 10]
        await live_push.live_push_cron({"redis": redis})
        assert len(fcm_calls) == 5

        for failures in (1, 1, 2):
            await live_push.live_push_cron({"redis": redis})
            assert await stored_match_ids() == ["123"]
            assert ticks["vlrgg:push:fetch_failures:123"] == failures
            assert "123" in json.loads(videos[constants.PUSH_DETAILS_KEY])
        await live_push.live_push_cron({"redis": redis})
        assert await stored_match_ids() == []
        assert not [key for key in ticks if key.startswith("vlrgg:push:fetch_failures:")]
        assert match_by_id_mock.await_count == 8
        end_payloads = [
            json.loads(request.content)["aps"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
        ]
        assert [aps["event"] for aps in end_payloads] == ["update", "end"]
        end_teams = end_payloads[-1]["content-state"]["teams"]
        assert [(team["tag"], team["score"]) for team in end_teams] == [("ALP", 1), ("BET", 0)]
        assert [team["tag"] for team in json.loads(fcm_calls[5][0].data["state"])["teams"]] == ["ALP", "BET"]
        assert any(request.method == "DELETE" for request in requests)
        assert len(fcm_calls) == 6
        assert json.loads(fcm_calls[5][0].data["state"])["terminal"] is True

        from app.api.v1.endpoints.live_updates import router

        async with sessions.begin() as session:
            await SubscriptionStore(session).add_favorites(client_id, Favorites(matches=[constants.TEST_MATCH_ID]))
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
            assert (await store.get_favorites(client_id)).matches == ["123", constants.TEST_MATCH_ID]
        assert sum("/3/device/aabb" in request.url.path for request in requests) == 2
        assert all(r.url.path.endswith("/aabb") for r in requests if "/3/device/" in r.url.path)
        assert started_before_send == [True, True]
        assert all(message.token == "fcm-token:APA91b" for call in fcm_calls for message in call)
        assert blocked_during_channel_create == [False, False]
        starts = [r for r in requests if "/3/device/" in r.url.path]
        assert starts and all(int(r.headers["apns-expiration"]) > time.time() + 300 for r in starts)
        channels = [r for r in requests if r.method == "POST" and r.url.path.endswith("/channels")]
        assert [json.loads(r.content)["message-storage-policy"] for r in channels] == [1, 1]
        test_events = [
            json.loads(request.content)["aps"]["event"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
        ]
        assert test_events == ["update", "end", "update", "update", "update", "update", "end"]
        assert [message.token for message in fcm_calls[-1]] == ["fcm-token:APA91b"]
        last_fcm_state = json.loads(fcm_calls[-1][0].data["state"])
        assert last_fcm_state["terminal"] is True
        assert last_fcm_state["total_maps"] == 3
        assert last_fcm_state["current_map"]["number"] == 1
        assert last_fcm_state["map_round_winners"] == [
            {"map_number": 1, "winners": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0]},
            {"map_number": 2, "winners": []},
            {"map_number": 3, "winners": []},
        ]
        assert "team_0" not in last_fcm_state and "team_1" not in last_fcm_state
        synthetic_end = [
            json.loads(request.content)["aps"]["content-state"]
            for request in requests
            if "/4/broadcasts/" in request.url.path
        ][-1]
        assert synthetic_end == {
            **last_fcm_state,
            "team_0": "test-alpha",
            "team_1": "test-beta",
            "map_rounds": ["01010101010", "", ""],
        }
        assert match_by_id_mock.await_count == 8

        from app import schemas
        from app.cron import jobs
        from app.db.models import Team

        async def stored_teams():
            async with sessions() as session:
                return {team.id: (team.name, team.tag, team.rank) for team in await session.scalars(select(Team))}

        assert await stored_teams() == {"1": ("Alpha", "ALP", None), "2": ("Beta", "BET", None)}
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
        from app.services import scrape_store

        async with sessions.begin() as session:
            await scrape_store.upsert_team(session, "1", name="Alpha", tag=None)
        assert (await stored_teams())["1"] == ("Alpha", "ALP", 3)

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

        listing.return_value = [SimpleNamespace(id="789", status=MatchStatus.LIVE)]
        fetches["789"] = [_live_detail("live", (0, 0)), _live_detail("live", (1, 0))]
        async with sessions.begin() as session:
            store = SubscriptionStore(session)
            await store.register_token(android_id, "fcm-token:APA91b", Platform.ANDROID, live_updates=False)
            await store.add_favorites(android_id, Favorites(matches=["789"]))
        sent = len(fcm_calls)
        await live_push.live_push_cron({"redis": redis})
        assert len(fcm_calls) == sent
        async with sessions.begin() as session:
            await SubscriptionStore(session).register_token(
                android_id, "fcm-token:APA91b", Platform.ANDROID, live_updates=True
            )
        await live_push.live_push_cron({"redis": redis})
        assert any(message.token == "fcm-token:APA91b" for call in fcm_calls[sent:] for message in call)

        video["observed_at"] = int(time.time())
        video["status"] = "ok"
        video["teams"][0]["score"], video["teams"][1]["score"] = 15, 14
        pushed = len(fcm_calls)
        assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed + 1
        changed_state = json.loads(fcm_calls[pushed][0].data["state"])
        ticks.pop(constants.PUSH_REFRESH_KEY.format("789"))
        await put_video()
        assert len(fcm_calls) == pushed + 2
        refreshed_state = json.loads(fcm_calls[pushed + 1][0].data["state"])
        assert refreshed_state["current_map"] == changed_state["current_map"]
        assert refreshed_state["teams"] == changed_state["teams"]
        await put_video()
        assert len(fcm_calls) == pushed + 2
        pushed = len(fcm_calls)
        ticks.pop(constants.PUSH_REFRESH_KEY.format("789"))
        newer = json.loads(videos[constants.VIDEO_SCORE_KEY])
        newer["teams"][0]["score"], newer["teams"][1]["score"] = 16, 14
        live_tokens = SubscriptionStore.live_android_tokens

        async def store_newer_during_token_read(store, routing):
            videos[constants.VIDEO_SCORE_KEY] = json.dumps(newer)
            return await live_tokens(store, routing)

        with monkeypatch.context() as boundary:
            boundary.setattr(SubscriptionStore, "live_android_tokens", store_newer_during_token_read)
            await put_video()
            assert len(fcm_calls) == pushed
            assert constants.PUSH_REFRESH_KEY.format("789") in ticks
        videos[constants.VIDEO_SCORE_KEY] = json.dumps(video)

        pushed = len(fcm_calls)
        video["pause"] = {"kind": "tech_pause", "reason": "GEAR", "since": 120}
        assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed + 1
        assert json.loads(fcm_calls[pushed][0].data["state"])["pause"] == {
            "kind": "tech_pause",
            "reason": "GEAR",
        }
        video.pop("pause")
        assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed + 2
        assert json.loads(fcm_calls[pushed + 1][0].data["state"])["pause"] is None
        video["pause"] = {"kind": "coffee", "reason": "", "since": 120}
        assert (await put_video()).status_code == 422
        assert json.loads(videos[constants.VIDEO_SCORE_KEY])["pause"] is None
        video.pop("pause")
        video["teams"][0]["code"], video["teams"][1]["code"] = "SAME", "SAME"
        assert (await put_video()).status_code == 422
        video["teams"][0]["code"], video["teams"][1]["code"] = "ALP", "XYZ"
        from app.schemas.matches import MatchData
        from app.schemas.matches import Team as MapTeam

        opening = _live_detail("live", (1, 0))
        opening.total_maps = 2
        opening.data.append(
            MatchData(
                number=2,
                map="Lotus",
                teams=[MapTeam(name="Alpha", score=0), MapTeam(name="Beta", score=0)],
                members=[],
                rounds=[],
            )
        )
        fetches["789"].append(opening)
        video["observed_at"] = int(time.time())
        video["map_number"] = 2
        video["teams"][0]["score"], video["teams"][1]["score"] = 0, 0
        video["pause"] = {"kind": "tech_pause", "reason": "GEAR", "since": 120}
        pushed = len(fcm_calls)
        assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed
        await live_push.live_push_cron({"redis": redis})
        sent_states = [json.loads(call[0].data["state"]) for call in fcm_calls[pushed:]]
        opening_state = next(state for state in sent_states if state["match_id"] == "789")
        assert opening_state["current_map"]["number"] == 1
        assert opening_state["pause"] == {"kind": "tech_pause", "reason": "GEAR"}
    finally:
        await apns.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_video_context_resolves_the_trackers_match(monkeypatch, tmp_path):
    import httpx2
    from fastapi import Depends, FastAPI

    from app.api import deps
    from app.api.v1.endpoints.video import router as video_router
    from app.schemas.matches import Event, MatchData, MatchVideos, MatchWithDetails, TeamWithImage

    token_file = tmp_path / "video-token"
    token_file.write_text("tracker-token")
    monkeypatch.setattr(deps.settings, "VIDEO_TOKEN_FILE", str(token_file))

    def cached(maps, status="live"):
        return MatchWithDetails(
            teams=[
                TeamWithImage(id="1", name="Alpha", tag="ALP", score=1, img="https://cdn.vlr.gg/a.png"),
                TeamWithImage(id="2", name="Beta", tag="BET", score=0, img="https://cdn.vlr.gg/b.png"),
            ],
            bans=[],
            event=Event(id="99", img="https://cdn.vlr.gg/e.png", series="Series", stage="Stage", status=status),
            videos=MatchVideos(streams=[], vods=[]),
            map_count=len(maps),
            data=[MatchData(number=number, map=name, teams=[], members=[], rounds=[]) for number, name in maps],
            previous_encounters=[],
        ).model_dump(mode="json")

    def video(codes, scores):
        return {
            "status": "ok",
            "observed_at": int(time.time()),
            "map_number": 4,
            "teams": [{"code": code, "name": code, "score": score} for code, score in zip(codes, scores, strict=True)],
        }

    videos = {
        constants.VIDEO_SCORE_KEY: json.dumps(video(["ALP", "BET"], [12, 9])),
        constants.PUSH_DETAILS_KEY: json.dumps(
            {
                "124": cached([(1, "Ascent"), (4, "Lotus")], "completed"),
                "123": cached([(1, "Ascent"), (4, "Lotus")]),
            }
        ),
    }
    redis = AsyncMock()
    redis.get.side_effect = lambda key: videos.get(key, DEFAULT)

    app = FastAPI()
    app.include_router(video_router, prefix="/api/v1/video", dependencies=[Depends(deps.verify_video_token)])
    app.dependency_overrides[deps.get_redis_client] = lambda: redis

    async def get_context(codes: str = ""):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as api:
            return await api.get(
                "/api/v1/video/context",
                params={"codes": codes} if codes else None,
                headers={"X-Video-Token": "tracker-token"},
            )

    response = await get_context()
    assert response.status_code == 200
    assert response.json() == {
        "match_id": "123",
        "map_number": 4,
        "map_order": ["Ascent", "", "", "Lotus"],
        "teams": ["ALP", "BET"],
    }
    del videos[constants.VIDEO_SCORE_KEY]
    response = await get_context("ALP,BET")
    assert response.status_code == 200
    assert response.json() == {
        "match_id": "123",
        "map_number": None,
        "map_order": ["Ascent", "", "", "Lotus"],
        "teams": ["ALP", "BET"],
    }
    assert (await get_context("ALP,Alpha")).status_code == 404
    videos[constants.PUSH_DETAILS_KEY] = json.dumps(
        {
            "123": cached([(1, "Ascent"), (4, "Lotus")]),
            "125": cached([(1, "Ascent"), (4, "Lotus")]),
        }
    )
    assert (await get_context("ALP,BET")).status_code == 404
