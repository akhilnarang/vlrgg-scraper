import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import DEFAULT, AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import constants
from app.constants import TEST_MATCH_ID
from app.cron import legacy_fcm, live_push, worker
from app.db.models import LiveActivityStart, MatchPushState
from app.enums import MatchStatus, Platform, TeamSide
from app.exceptions import ScrapingError
from app.schemas.matches import Favorites, Match, MatchTeam


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
                Round(round_number=3, round_score="2-1", winner="team1", side="attack", win_type="Elimination"),
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

    video.teams[0].side, video.teams[1].side = TeamSide.RED, TeamSide.BLUE
    push.order_teams_for_broadcast(detail, video)
    assert [team.name for team in detail.teams] == ["Alpha", "Beta"]

    video.map_number = 4
    push.order_teams_for_broadcast(detail, video)
    state = push.project_state("123", detail)
    assert state is not None and state.current_map is not None
    assert [team.name for team in state.teams] == ["Beta", "Alpha"]
    assert state.current_map.scores == [4, 8]
    assert state.map_round_winners == [
        PushMapRounds(map_number=1, scores=[9, 13]),
        PushMapRounds(map_number=2),
        PushMapRounds(map_number=3),
        PushMapRounds(map_number=4, winners=[1, None, 1], scores=[4, 8]),
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


def test_finished_maps_floor_the_series_score_and_drop_unplayed_rounds():
    """A finished map must reach the series score and its timeline must end at the deciding round.

    VLR's top header still showing 1-1 must not hide map 3's confirmed 13-1 win, and the
    unplayed round columns VLR renders must not trail the finished map's round list.
    """
    from app.schemas.matches import MatchData, PushMapRounds, Round, Team
    from app.services import push

    detail = _live_detail("live", (1, 1))
    detail.total_maps = 3
    detail.data = [
        MatchData(
            number=1,
            map="Ascent",
            teams=[Team(name="Alpha", score=13), Team(name="Beta", score=4)],
            members=[],
            rounds=[],
        ),
        MatchData(
            number=2,
            map="Bind",
            teams=[Team(name="Alpha", score=2), Team(name="Beta", score=13)],
            members=[],
            rounds=[],
        ),
        MatchData(
            number=3,
            map="Lotus",
            teams=[Team(name="Alpha", score=13), Team(name="Beta", score=1)],
            members=[],
            rounds=[
                Round(round_number=number, round_score="", winner="team1", side="", win_type="Elimination")
                for number in range(1, 15)
            ]
            + [
                Round(round_number=number, round_score="", winner="", side="", win_type="Not Played")
                for number in range(15, 25)
            ],
        ),
    ]

    state = push.project_state("123", detail)

    assert state is not None
    assert state.map_winners == ["1", "2", "1"]
    # The confirmed two map wins make it 2-1 while the header still shows 1-1.
    assert [team.score for team in state.teams] == [2, 1]
    assert state.map_round_winners == [
        PushMapRounds(map_number=1, scores=[13, 4]),
        PushMapRounds(map_number=2, scores=[2, 13]),
        PushMapRounds(map_number=3, winners=[0] * 14, scores=[13, 1]),
    ]


@pytest.mark.asyncio
async def test_live_push_cron_applies_video_score_only_to_resolved_match(monkeypatch, caplog):
    from redis.exceptions import RedisError

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

    clock = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    class FakeRedis:
        """Redis stand-in whose NX writes expire against the test's clock."""

        def __init__(self):
            self.values = {
                constants.VIDEO_CONTEXT_KEY: b"456",
                constants.VIDEO_FALLBACK_KEY.format("456"): str(clock[0] - 1600).encode(),
            }
            self.expires = {}

        async def get(self, key):
            self.expire_due()
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            self.expire_due()
            if nx and key in self.values:
                return None
            self.values[key] = value if isinstance(value, bytes) else str(value).encode()
            if ex is not None:
                self.expires[key] = clock[0] + ex
            return True

        async def expire(self, key, ttl):
            self.expires[key] = clock[0] + ttl
            return True

        async def delete(self, *keys):
            for key in keys:
                self.values.pop(key, None)
                self.expires.pop(key, None)
            return True

        def expire_due(self):
            for key, deadline in list(self.expires.items()):
                if deadline <= clock[0]:
                    self.values.pop(key, None)
                    self.expires.pop(key, None)

    redis = FakeRedis()
    caplog.set_level(logging.WARNING)

    def alerts():
        return [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]

    async def run_cron(seconds=0):
        clock[0] += seconds
        await live_push.live_push_cron({"redis": redis})

    await run_cron()

    assert [(state.match_id, state.current_map.scores if state.current_map else None) for state in sent] == [
        ("123", [8, 4]),
        ("456", [0, 0]),
    ]
    assert sent[0].pause is not None and (sent[0].pause.kind, sent[0].pause.reason) == ("tech_pause", "GEAR")
    assert sent[1].pause is None
    mark_delivered.assert_awaited_once_with(redis, "123", video)
    # The tracker asked context for 456 but VLR has pushed it longer than a break between maps.
    assert len(alerts()) == 1 and "456" in alerts()[0]

    # Further stalled runs inside the alert's hour stay throttled by the NX alert key.
    await run_cron(60)
    await run_cron(60)
    assert len(alerts()) == 1

    # Once the tracker covers the match again, the gap clears without another alert.
    video.map_number = 2
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value="456"))
    caplog.clear()
    marked = mark_delivered.await_count
    await run_cron(60)
    assert constants.VIDEO_FALLBACK_KEY.format("456") not in redis.values
    assert mark_delivered.await_count == marked
    assert alerts() == []

    # A stall that has only just begun is below the alert threshold, so no run alerts.
    redis.values[constants.VIDEO_CONTEXT_KEY] = b"789"
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value=None))
    caplog.clear()
    for _ in range(3):
        await run_cron(240)
    assert alerts() == []

    # The same stall crosses the threshold once, holds for its hour, and re-arms after it.
    for _ in range(5):
        await run_cron(240)
    assert len(alerts()) == 1 and "789" in alerts()[0]
    for _ in range(12):
        await run_cron(240)
    assert len(alerts()) == 1
    for _ in range(4):
        await run_cron(240)
    assert len(alerts()) == 2 and "789" in alerts()[1]

    # A tracker asking for codes no VLR match fits is a fault of its own once the wait is sustained.
    caplog.clear()
    redis.values[constants.VIDEO_CODES_KEY] = b"FLY,MIBR"
    redis.values[constants.VIDEO_UNRESOLVED_KEY] = json.dumps(
        {"codes": "FLY,MIBR", "since": clock[0] - constants.VIDEO_UNRESOLVED_ALERT_SECONDS - 60}
    ).encode()
    await run_cron(60)
    assert len(alerts()) == 1 and "FLY,MIBR" in alerts()[0]

    # The unresolved alert stays throttled inside its window.
    await run_cron(60)
    assert len(alerts()) == 1

    # A tracker that stopped asking is systemd's to notice, so an old mark without liveness does not alert.
    redis.values.pop(constants.VIDEO_CODES_KEY)
    redis.values.pop(constants.VIDEO_UNRESOLVED_ALERTED_KEY)
    caplog.clear()
    await run_cron(60)
    assert alerts() == []

    # A Redis error while checking the gap must not stop the run's pushes.
    caplog.clear()
    pushed = len(sent)
    read = redis.get

    async def broken_get(key):
        if key == constants.VIDEO_CONTEXT_KEY:
            raise RedisError("redis unavailable")
        return await read(key)

    monkeypatch.setattr(redis, "get", broken_get)
    await run_cron(240)
    assert [state.match_id for state in sent[pushed:]] == ["123", "456"]
    assert "could not check the video tracker gap" in [record.message for record in caplog.records]


@pytest.mark.asyncio
async def test_final_video_owned_match_gets_terminal_push_and_cleanup(monkeypatch, make_database):
    """A video-owned match VLR marks final still gets the cron's terminal push and state cleanup."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core import connections
    from app.db.migrations import upgrade_to_head
    from app.services import live_store, push
    from app.services.subscription_store import SubscriptionStore

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    android_id = "22222222-2222-4222-8222-222222222222"
    stale = _live_detail("live", (1, 0))
    final = _live_detail("completed", (1, 0))
    async with sessions.begin() as session:
        store = SubscriptionStore(session)
        await store.register_token(android_id, "fcm-token:APA91b", Platform.ANDROID)
        await store.add_favorites(android_id, Favorites(matches=["123"]))
        await store.save_match("123", None, None)
        await live_store.upsert_live_detail(session, "123", final.model_dump(mode="json"), int(time.time()))

    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": stale.model_dump(mode="json")})}
    written = {}

    def write(key, value, **_kwargs):
        written[key] = value
        return True

    redis = AsyncMock()
    redis.get.side_effect = lambda key: values.get(key)
    redis.set.side_effect = write
    sent = []

    async def publish_direct(app, tokens, state):
        sent.append(state)
        return ["message:1"]

    async def fetch_detail(client, match_id):
        return final

    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", "configured")
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(connections, "apns_client", None)
    monkeypatch.setattr(push, "video_score", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value="123"))
    monkeypatch.setattr(live_push, "_listed_live_ids", AsyncMock(return_value={"123"}))
    monkeypatch.setattr(live_push, "_fetch_detail", fetch_detail)
    monkeypatch.setattr(live_push, "_store_teams", AsyncMock())
    monkeypatch.setattr(live_push.fcm, "get_app", lambda: object())
    monkeypatch.setattr(live_push.fcm, "publish_direct", publish_direct)

    try:
        await live_push.live_push_cron({"redis": redis})

        assert [(state.match_id, state.terminal) for state in sent] == [("123", True)]
        async with sessions() as session:
            assert await SubscriptionStore(session).get_match("123") is None
            assert await live_store.get_live_match(session, "123") is None
        # The finished match leaves the video's resolution cache.
        assert json.loads(written[constants.PUSH_DETAILS_KEY]) == {}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("page_gone", [False, True], ids=["terminal-fetch", "page-gone"])
async def test_failed_live_copy_cleanup_keeps_push_state_deleted(monkeypatch, make_database, page_gone):
    """An aborted live-copy cleanup must keep the push-state deletion committed and the match unresolvable."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core import connections
    from app.db.migrations import upgrade_to_head
    from app.schemas.matches import CompactState, PushTeam, VideoScore
    from app.services import live_store, push
    from app.services.subscription_store import SubscriptionStore

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    android_id = "22222222-2222-4222-8222-222222222222"
    final = _live_detail("completed", (1, 0))
    # The page-gone ending reads a copy still showing a live match; a successful fetch stores the final one.
    stored = _live_detail("live", (1, 0)) if page_gone else final
    played = CompactState(
        match_id="123",
        observed_at=int(time.time()),
        terminal=False,
        teams=[PushTeam(id="1", name="Alpha", score=1), PushTeam(id="2", name="Beta", score=0)],
    )
    async with sessions.begin() as session:
        store = SubscriptionStore(session)
        await store.register_token(android_id, "fcm-token:APA91b", Platform.ANDROID)
        await store.add_favorites(android_id, Favorites(matches=["123"]))
        # The page-gone ending pushes the stored last sent score; the final fetch already carries one.
        await store.save_match("123", None, played.semantic() if page_gone else None)
        await live_store.upsert_live_detail(session, "123", stored.model_dump(mode="json"), int(time.time()))

    values = {constants.PUSH_DETAILS_KEY: json.dumps({"123": _live_detail("live", (1, 0)).model_dump(mode="json")})}

    def write(key, value, **_kwargs):
        values[key] = value
        return True

    redis = AsyncMock()
    redis.get.side_effect = lambda key: values.get(key)
    redis.set.side_effect = write
    sent = []

    async def publish_direct(app, tokens, state):
        sent.append(state)
        return ["message:1"]

    async def fetch_detail(client, match_id):
        if page_gone:
            raise ScrapingError(upstream_status=404)
        return final

    async def fail_delete(session, match_id):
        # A database error aborts the live copy's cleanup transaction; the required push-state
        # deletion must already be committed to survive it.
        await session.rollback()
        raise SQLAlchemyError("database is locked")

    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", "configured")
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(connections, "apns_client", None)
    monkeypatch.setattr(push, "video_score", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value=None if page_gone else "123"))
    monkeypatch.setattr(live_push, "_listed_live_ids", AsyncMock(return_value=set() if page_gone else {"123"}))
    monkeypatch.setattr(live_push, "_fetch_detail", fetch_detail)
    monkeypatch.setattr(live_push, "_store_teams", AsyncMock())
    monkeypatch.setattr(live_push.fcm, "get_app", lambda: object())
    monkeypatch.setattr(live_push.fcm, "publish_direct", publish_direct)
    monkeypatch.setattr(live_store, "delete_live_match", fail_delete)

    try:
        await live_push.live_push_cron({"redis": redis})

        assert [state.terminal for state in sent] == [True]
        # The committed deletion is durable even though the live copy's cleanup was rolled back.
        async with sessions() as session:
            assert (await session.scalars(select(MatchPushState.match_id))).all() == []
        async with sessions() as session:
            surviving = await live_store.get_live_match(session, "123")
        # The failed deletion left the row, but its details were retired first, so nothing can resolve it.
        assert surviving is not None and surviving.detail is None

        # A later tracker update must not resolve the surviving row, resend a final, or resurrect the match.
        values.pop(constants.PUSH_DETAILS_KEY)  # a cold cache, e.g. right after a restart
        video = VideoScore.model_validate(
            {
                "status": "ok",
                "observed_at": int(time.time()),
                "map_number": 1,
                "teams": [
                    {"code": "ALP", "name": "Alpha", "score": 13},
                    {"code": "BET", "name": "Beta", "score": 9},
                ],
            }
        )
        monkeypatch.setattr(push, "video_score", AsyncMock(return_value=video))
        monkeypatch.setattr(live_push.cache, "get_client", lambda: redis)
        await live_push.push_video_match()

        assert [state.terminal for state in sent] == [True]
        async with sessions() as session:
            assert await SubscriptionStore(session).get_match("123") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "listed"),
    [
        pytest.param(ValueError("bad markup"), set(), id="parser-error"),
        pytest.param(ScrapingError(upstream_status=429), set(), id="rate-limited"),
        pytest.param(ScrapingError(upstream_status=502), {"123"}, id="listed-live"),
        pytest.param(ScrapingError(upstream_status=502), None, id="listing-unavailable"),
    ],
)
async def test_live_push_cron_only_ends_on_unlisted_vlr_fetch_failures(monkeypatch, error, listed):
    """A parser error, a rate limit, a fetch failure while VLR lists the match live, or no listing never ends it."""
    from app.core import connections
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
            return ["123"]

    async def fetch_detail(client, match_id):
        raise error

    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", None)
    monkeypatch.setattr(connections, "subscription_sessions", Session)
    monkeypatch.setattr(live_push, "SubscriptionStore", Store)
    monkeypatch.setattr(push, "video_score", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_listed_live_ids", AsyncMock(return_value=listed))
    monkeypatch.setattr(live_push, "_fetch_detail", fetch_detail)
    monkeypatch.setattr(live_push, "_cache_details", AsyncMock())
    monkeypatch.setattr(live_push, "_store_teams", AsyncMock())
    end = AsyncMock()
    monkeypatch.setattr(live_push, "_end_unavailable_match", end)
    redis = AsyncMock()
    redis.get.return_value = None

    for _ in range(constants.PUSH_FETCH_FAILURE_LIMIT + 1):
        await live_push.live_push_cron({"redis": redis})

    end.assert_not_awaited()
    redis.incr.assert_not_awaited()
    # The non-consecutive failure streaks the match may have had are cleared.
    redis.delete.assert_awaited_with(constants.PUSH_FETCH_FAILURES_KEY.format("123"))


@pytest.mark.asyncio
@pytest.mark.parametrize("page_gone", [True, False], ids=["page-gone", "completed-scoreless"])
async def test_live_push_cron_drops_an_unstarted_match_without_a_final_push(monkeypatch, page_gone):
    """A tracked match whose state has no play must not announce a fake "FINAL 0-0"."""
    from app.core import connections
    from app.schemas.matches import CompactState, PushTeam
    from app.services import push

    events = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def commit(self):
            events.append("commit")

        async def execute(self, _statement):
            pass

    unstarted = CompactState(
        match_id="123",
        observed_at=int(time.time()),
        terminal=False,
        teams=[PushTeam(id="1", name="Alpha", score=0), PushTeam(id="2", name="Beta", score=0)],
    )
    deleted = []

    class Store:
        def __init__(self, session):
            pass

        async def list_match_ids(self):
            return ["123"]

        async def get_match(self, match_id):
            return SimpleNamespace(channel_id="channel-1", last_state_json=unstarted.semantic())

        async def live_android_tokens(self, routing):
            return ["fcm-token:APA91b"]

        async def delete_match(self, match_id):
            deleted.append(match_id)

    completed = _live_detail("completed", (0, 0))
    completed.data = []

    async def fetch_detail(client, match_id):
        if page_gone:
            raise ScrapingError(upstream_status=404)
        return completed

    ends = []
    channels = []

    async def publish(channel_id, state, terminal, *, immediate_dismissal):
        events.append("publish")
        ends.append((channel_id, state, terminal, immediate_dismissal))

    async def delete_channel(channel_id):
        events.append("delete_channel")
        channels.append(channel_id)

    apns = SimpleNamespace(publish=publish, delete_channel=delete_channel)

    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", "configured")
    monkeypatch.setattr(connections, "subscription_sessions", Session)
    monkeypatch.setattr(connections, "apns_client", apns)
    monkeypatch.setattr(live_push, "SubscriptionStore", Store)
    monkeypatch.setattr(push, "video_score", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_listed_live_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(live_push, "_fetch_detail", fetch_detail)
    monkeypatch.setattr(live_push, "_cache_details", AsyncMock())
    monkeypatch.setattr(live_push, "_store_teams", AsyncMock())
    monkeypatch.setattr(live_push.fcm, "get_app", lambda: object())
    publish_direct = AsyncMock()
    monkeypatch.setattr(live_push.fcm, "publish_direct", publish_direct)
    redis = AsyncMock()
    redis.get.return_value = None

    await live_push.live_push_cron({"redis": redis})

    publish_direct.assert_not_awaited()
    assert [(channel, terminal, immediate) for channel, _, terminal, immediate in ends] == [("channel-1", True, True)]
    state = ends[0][1]
    assert state.terminal and state.current_map is None
    assert [team.score for team in state.teams] == [0, 0]
    assert channels == ["channel-1"]
    assert deleted == ["123"]
    # The session's write is committed before the APNs end and channel deletion.
    assert events.index("commit") < events.index("publish") < events.index("delete_channel")


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
async def test_live_push_cron_starts_updates_and_ends_match(monkeypatch, tmp_path, caplog, make_database):
    import httpx2
    from firebase_admin import messaging
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core import connections
    from app.db.migrations import upgrade_to_head
    from app.services import apns as apns_service
    from app.services import live_store
    from app.services.subscription_store import SubscriptionStore

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
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
            _live_detail("upcoming", (1, 0)),
            ScrapingError(upstream_status=502),
            httpx2.ConnectTimeout("timed out"),
            ScrapingError(upstream_status=503),
            ScrapingError(upstream_status=500),
            ScrapingError(upstream_status=500),
        ],
        "456": [ScrapingError(upstream_status=404)],
    }

    async def fetch(match_id, redis_client):
        outcome = fetches[match_id].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(live_push.matches, "get_upcoming_matches", AsyncMock(side_effect=[[listed]] * 4 + [[]] * 5))
    match_by_id_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(live_push.matches, "match_by_id", match_by_id_mock)
    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", "configured")

    created_sessions = []

    class TrackingSessions:
        """Session factory the app opens, recording every session it hands out."""

        def __call__(self):
            session = sessions()
            created_sessions.append(session)
            return session

        def begin(self):
            return sessions.begin()

    def session_in_transaction() -> bool:
        """Whether a database session the app opened is still holding a transaction."""
        return any(session.in_transaction() for session in created_sessions)

    requests = []
    started_before_send = []
    in_transaction_during_apns = []

    async def handler(request):
        requests.append(request)
        in_transaction_during_apns.append(session_in_transaction())
        if request.method == "POST" and request.url.path.endswith("/channels"):
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
    monkeypatch.setattr(connections, "subscription_sessions", TrackingSessions())
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
        if key.startswith(("valesports:tracker:", constants.PUSH_DETAILS_KEY)):
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
    redis.get.side_effect = lambda key: videos.get(key, DEFAULT if key == "matches" else None)
    redis.set.side_effect = set_tick
    redis.exists.side_effect = lambda key: key in ticks
    redis.delete.side_effect = lambda key: ticks.pop(key, None)
    redis.incr.side_effect = increment
    from tests.test_video_round_history import MemoryPipeline

    redis.pipeline = lambda **_kwargs: MemoryPipeline(redis)

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
            live = await live_store.get_live_match(session, "123")
        assert row is not None and row.channel_id == "channel-123"
        assert live is not None and live.detail is not None and live.detail_fetched_at is not None
        assert [team["tag"] for team in live.detail["teams"]] == ["ALP", "BET"]
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
        async with sessions() as session:
            live = await live_store.get_live_match(session, "123")
        assert live is not None and live.video is not None and live.video_received_at is not None
        assert [team["code"] for team in live.video["teams"]] == ["ALP", "XYZ"]
        assert match_by_id_mock.await_count == vlr_fetches
        assert (await put_video()).status_code == 204
        await live_push.live_push_cron({"redis": redis})
        # The cron still fetches the video-tracked match's details, but leaves its pushes to the video.
        assert len(fcm_calls) == 2 and match_by_id_mock.await_count == vlr_fetches + 1
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

        for failures in (1, None, 1):
            await live_push.live_push_cron({"redis": redis})
            assert await stored_match_ids() == ["123"]
            assert ticks.get("valesports:push:fetch_failures:123") == failures
            assert "123" in json.loads(videos[constants.PUSH_DETAILS_KEY])
        await live_push.live_push_cron({"redis": redis})
        assert await stored_match_ids() == ["123"]
        assert ticks["valesports:push:fetch_failures:123"] == 2
        await live_push.live_push_cron({"redis": redis})
        assert await stored_match_ids() == []
        async with sessions() as session:
            assert await live_store.get_live_match(session, "123") is None
        assert not [key for key in ticks if key.startswith("valesports:push:fetch_failures:")]
        assert match_by_id_mock.await_count == 10
        end_payloads = [
            json.loads(request.content)["aps"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
        ]
        assert [aps["event"] for aps in end_payloads] == ["update", "update", "end"]
        assert all("dismissal-date" not in aps for aps in end_payloads if aps["event"] == "update")
        # The final card stays the four hours ActivityKit allows; a shorter window hid the result from
        # followers who checked their phone later.
        assert end_payloads[-1]["dismissal-date"] == end_payloads[-1]["timestamp"] + 4 * 60 * 60
        end_teams = end_payloads[-1]["content-state"]["teams"]
        assert [(team["tag"], team["score"]) for team in end_teams] == [("ALP", 1), ("BET", 0)]
        assert [team["tag"] for team in json.loads(fcm_calls[5][0].data["state"])["teams"]] == ["ALP", "BET"]
        assert any(request.method == "DELETE" for request in requests)
        assert len(fcm_calls) == 6
        assert json.loads(fcm_calls[5][0].data["state"])["terminal"] is True
        # No APNs request may start while the app still has a database transaction open.
        assert in_transaction_during_apns and not any(in_transaction_during_apns)

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
        starts = [r for r in requests if "/3/device/" in r.url.path]
        assert starts and all(int(r.headers["apns-expiration"]) > time.time() + 300 for r in starts)
        channels = [r for r in requests if r.method == "POST" and r.url.path.endswith("/channels")]
        assert [json.loads(r.content)["message-storage-policy"] for r in channels] == [1, 1]
        test_events = [
            json.loads(request.content)["aps"]["event"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
        ]
        assert test_events == ["update", "update", "end", "update", "update", "update", "update", "end"]
        assert [message.token for message in fcm_calls[-1]] == ["fcm-token:APA91b"]
        last_fcm_state = json.loads(fcm_calls[-1][0].data["state"])
        assert last_fcm_state["terminal"] is True
        assert last_fcm_state["total_maps"] == 3
        assert last_fcm_state["current_map"]["number"] == 1
        assert last_fcm_state["map_round_winners"] == [
            {"map_number": 1, "winners": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0], "scores": [6, 5]},
            {"map_number": 2, "winners": [], "scores": []},
            {"map_number": 3, "winners": [], "scores": []},
        ]
        assert match_by_id_mock.await_count == 10

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
            await store.add_favorites(client_id, Favorites(matches=["789"]))
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
        # Within the tracker lead bound of VLR's 13-9 map, so the scores still advance the display.
        video["teams"][0]["score"], video["teams"][1]["score"] = 15, 9
        pushed = len(fcm_calls)
        assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed + 1
        cached_vlr = videos[constants.PUSH_DETAILS_KEY]
        vlr_fetches = match_by_id_mock.await_count
        video["teams"][0]["score"] = 16
        pushed = len(fcm_calls)
        assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed + 1
        assert videos[constants.PUSH_DETAILS_KEY] == cached_vlr
        assert match_by_id_mock.await_count == vlr_fetches
        apns_state = [
            json.loads(request.content)["aps"]["content-state"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
            and json.loads(request.content)["aps"]["content-state"]["match_id"] == "789"
        ][-1]
        assert apns_state["current_map"]["scores"] == [16, 9]
        # Only the first team scored past VLR's 13-9, so the score alone credits it rounds 23 to 25.
        assert apns_state["map_round_winners"][0]["winners"] == [None] * 22 + [0, 0, 0]
        changed_state = json.loads(fcm_calls[pushed][0].data["state"])
        assert changed_state["map_round_winners"] == apns_state["map_round_winners"]
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
        newer["teams"][0]["score"], newer["teams"][1]["score"] = 17, 14
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

        async with sessions() as session:
            live = await live_store.get_live_match(session, "789")
        assert live is not None and live.detail is not None
        videos.pop(constants.PUSH_DETAILS_KEY)  # a cold cache, e.g. right after a restart
        video["teams"][1]["score"] = 1
        vlr_fetches = match_by_id_mock.await_count
        pushed = len(fcm_calls)
        assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed + 1
        assert match_by_id_mock.await_count == vlr_fetches
        # The cold cache still resolves the match from SQLite, so the new round's winner is recorded.
        cold_state = json.loads(fcm_calls[pushed][0].data["state"])
        assert (cold_state["current_map"]["number"], cold_state["current_map"]["scores"]) == (2, [0, 1])
        cold_map = next(item for item in cold_state["map_round_winners"] if item["map_number"] == 2)
        assert cold_map["winners"] == [1]
        async with sessions() as session:
            live = await live_store.get_live_match(session, "789")
        assert live is not None and live.video is not None
        assert live.video["teams"][1]["score"] == 1

        # A failed live-store write warns and must not block FCM or APNs delivery.
        async def fail_upsert(*_args, **_kwargs):
            raise SQLAlchemyError("database is locked")

        video["teams"][1]["score"] = 2
        video["observed_at"] = int(time.time())
        vlr_fetches = match_by_id_mock.await_count
        pushed = len(fcm_calls)
        caplog.clear()
        with caplog.at_level(logging.WARNING), monkeypatch.context() as boundary:
            boundary.setattr(live_store, "upsert_live_video", fail_upsert)
            assert (await put_video()).status_code == 204
        assert len(fcm_calls) == pushed + 1
        assert match_by_id_mock.await_count == vlr_fetches
        assert [
            (record.levelno, record.message) for record in caplog.records if record.name == live_push.logger.name
        ] == [(logging.WARNING, "could not store video score for match 789")]
        apns_scores = [
            json.loads(request.content)["aps"]["content-state"]["current_map"]["scores"]
            for request in requests
            if request.url.path.endswith("/broadcasts/apps/com.example.app")
            and json.loads(request.content)["aps"]["content-state"]["match_id"] == "789"
        ]
        assert apns_scores[-1] == [0, 2]
    finally:
        await apns.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_video_context_resolves_the_trackers_match(monkeypatch, tmp_path):
    import httpx2
    from fastapi import Depends, FastAPI
    from redis.exceptions import RedisError

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
    redis.get.return_value = None
    redis.get.side_effect = lambda key: videos.get(key, DEFAULT if key == "matches" else None)

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
    redis.set.assert_awaited_with(constants.VIDEO_CONTEXT_KEY, "123", ex=constants.VIDEO_CONTEXT_TTL)
    # Every codes request records the tracker's liveness, so the cron can see it is still asking.
    redis.set.assert_any_await(constants.VIDEO_CODES_KEY, "ALP,BET", ex=constants.VIDEO_CODES_TTL)
    redis.delete.assert_awaited_with(constants.VIDEO_UNRESOLVED_KEY)

    # Codes that fit no match record when they stopped resolving; resolving them clears the mark.
    response = await get_context("ALP,Alpha")
    assert response.status_code == 404
    unresolved = [call.args[1] for call in redis.set.await_args_list if call.args[0] == constants.VIDEO_UNRESOLVED_KEY]
    assert unresolved and json.loads(unresolved[-1])["codes"] == "ALP,Alpha"
    assert (await get_context("ALP,BET")).status_code == 200
    redis.delete.assert_awaited_with(constants.VIDEO_UNRESOLVED_KEY)

    # Recording the context is best-effort: a failed write must not deny the tracker its answer.
    redis.set.side_effect = RedisError("redis unavailable")
    response = await get_context("ALP,BET")
    assert response.status_code == 200
    assert response.json()["match_id"] == "123"
    assert (await get_context("ALP,Alpha")).status_code == 404
    videos[constants.PUSH_DETAILS_KEY] = json.dumps(
        {
            "123": cached([(1, "Ascent"), (4, "Lotus")]),
            "125": cached([(1, "Ascent"), (4, "Lotus")]),
        }
    )
    assert (await get_context("ALP,BET")).status_code == 404


@pytest.mark.asyncio
async def test_riot_matches_cron_archives_games_then_links_them_when_a_vlr_map_appears(monkeypatch, make_database):
    """Every completed Riot game is archived, and an unlinked one is linked once VLR has its map."""
    import httpx2
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import schemas
    from app.core import connections
    from app.cron import riot_matches
    from app.db.migrations import upgrade_to_head
    from app.db.models import RiotMatch
    from app.schemas.matches import MatchData, Team, TeamMember
    from app.schemas.riot import GameLink, VlrTeam
    from app.services import riot

    database_url = make_database()
    await upgrade_to_head(database_url)
    sessions = async_sessionmaker(create_async_engine(database_url), expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(riot_matches.settings, "LLM_API_KEY", "test-key")

    scheduled = datetime(2026, 10, 17, 7, 0, tzinfo=ZoneInfo("UTC"))
    detail = _live_detail("completed", (2, 0))
    alpha = [TeamMember.model_construct(name=f"a{i}", team="ALP") for i in range(5)]
    beta = [TeamMember.model_construct(name=f"b{i}", team="Beta") for i in range(5)]
    # The Summit panel lists Beta first; Riot's Red side, which won 13, must resolve to Beta.
    detail.data = [
        MatchData(
            number=1,
            map="Summit",
            teams=[Team(name="Beta", score=13), Team(name="Alpha", score=5)],
            members=beta + alpha,
            rounds=[],
        ),
        MatchData(
            number=2,
            map="Bind",
            teams=[Team(name="Alpha", score=13), Team(name="Beta", score=11)],
            members=alpha + beta,
            rounds=[],
        ),
        MatchData(
            number=3,
            map="Lotus",
            teams=[Team(name="Beta", score=13), Team(name="Alpha", score=9)],
            members=beta + alpha,
            rounds=[],
        ),
        # VLR still shows Ascent live at 13-11, and Haven unfinished at 5-5; neither is a candidate yet.
        MatchData(
            number=4,
            map="Ascent",
            teams=[Team(name="Alpha", score=13), Team(name="Beta", score=11)],
            members=alpha + beta,
            rounds=[],
            live=True,
        ),
        MatchData(
            number=5,
            map="Haven",
            teams=[Team(name="Alpha", score=5), Team(name="Beta", score=5)],
            members=alpha + beta,
            rounds=[],
        ),
    ]
    # The match is listed twice; its maps are still each checked, and linked, once.
    listing = 2 * [
        schemas.Match(
            id="123",
            team1=schemas.MatchTeam(name="Alpha"),
            team2=schemas.MatchTeam(name="Beta"),
            status=MatchStatus.COMPLETED,
            time=scheduled,
            event="Event",
            series="Series",
        )
    ]

    def game(map_id, red, blue, minutes):
        return {
            "matchInfo": {
                "mapId": f"/Game/Maps/{map_id}/{map_id}",
                "isCompleted": True,
                "gameStartMillis": int((scheduled + timedelta(minutes=minutes)).timestamp() * 1000),
            },
            "players": [{"gameName": f"BET b{i}", "tagLine": "BET", "teamId": "Red"} for i in range(5)]
            + [{"gameName": f"ALP a{i}", "tagLine": "ALP", "teamId": "Blue"} for i in range(5)],
            "teams": [{"teamId": "Red", "roundsWon": red}, {"teamId": "Blue", "roundsWon": blue}],
            "roundResults": [
                {
                    "roundNum": number,
                    "roundResult": "Eliminated",
                    "winningTeam": "Red" if number <= red else "Blue",
                    "winningTeamRole": "attacker",
                    "roundCeremony": "Default",
                }
                for number in range(1, red + blue + 1)
            ],
        }

    games = {
        "linked": game("Plummet", 13, 5, 10),
        "no-vlr-map": game("Plummet", 13, 7, 10),  # no VLR map scored 13-7 yet
        "unsure": game("Duality", 11, 13, 60),  # fits Bind, but the verdict is below the threshold
        # A voided game and its replay both fit Lotus; neither is linked.
        "voided": game("Jam", 13, 9, 100),
        "replay": game("Jam", 13, 9, 140),
        "still-live": game("Ascent", 13, 11, 180),
        "aborted": game("Triad", 5, 5, 200),
        # A codename RIOT_MAP_NAMES lacks is archived without a link, not dropped.
        "unknown-map": game("Void", 13, 3, 220),
        # Missing a side's teams leaves nothing to link, but the completed game is still archived.
        "no-sides": {
            "matchInfo": {
                "mapId": "/Game/Maps/Ascent/Ascent",
                "isCompleted": True,
                "gameStartMillis": int((scheduled + timedelta(minutes=240)).timestamp() * 1000),
            },
            "players": [{"gameName": f"BET b{i}", "tagLine": "BET", "teamId": "Red"} for i in range(5)],
            "teams": [{"teamId": "Red", "roundsWon": 13}],
            "roundResults": [{"roundNum": 1, "roundResult": "Eliminated", "winningTeam": "Red"}],
        },
        # Completed, but without any round results yet: not useful archive data, so a later run retries it.
        "empty": {
            "matchInfo": {
                "mapId": "/Game/Maps/Split/Split",
                "isCompleted": True,
                "gameStartMillis": int((scheduled + timedelta(minutes=300)).timestamp() * 1000),
            },
            "players": [],
            "teams": [],
            "roundResults": [],
        },
    }
    pages = {"recent-matches/by-queue/tournamentmode": {"matchIds": list(games)}}
    pages |= {f"matches/{riot_id}": data for riot_id, data in games.items()}

    requested = []

    async def get(url, *_args, **_kwargs):
        requested.append(url)
        if isinstance(pages[url], int):
            return httpx2.Response(pages[url], request=httpx2.Request("GET", url))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: pages[url])

    verdicts = {
        1: GameLink(red_team="team1", blue_team="team2", same_game=True, confidence=0.99, reason="rosters"),
        2: GameLink(red_team="team2", blue_team="team1", same_game=True, confidence=0.8, reason="unsure"),
        3: GameLink(red_team="team1", blue_team="team2", same_game=True, confidence=0.99, reason="rosters"),
        6: GameLink(red_team="team1", blue_team="team2", same_game=True, confidence=0.99, reason="rosters"),
    }
    decide = AsyncMock(side_effect=lambda _game, candidate: verdicts[candidate.map_number])
    monkeypatch.setattr(riot, "decide", decide)
    fetch = AsyncMock(return_value=detail)
    monkeypatch.setattr(riot.matches, "cached_match_by_id", fetch)
    values = {"matches": schemas.MatchListAdapter.dump_json(listing)}
    redis = AsyncMock()
    redis.get.side_effect = values.get
    redis.set.side_effect = lambda key, value, **_kwargs: values.update({key: value})
    redis.mget.side_effect = lambda keys: [values.get(key) for key in keys]

    with patch("httpx2.AsyncClient.get", side_effect=get):
        await riot_matches.riot_matches_cron({"redis": redis})
        # VLR's page is fetched once per run, however many games and listings point at it.
        assert fetch.await_count == 1

    async with sessions() as session:
        stored = list((await session.execute(select(RiotMatch))).scalars())
    by_id = {row.riot_id: row for row in stored}
    # Every completed game with round results is archived, linked or not; the empty payload waits for rounds.
    assert set(by_id) == set(games) - {"empty"}
    assert (by_id["linked"].vlr_match_id, by_id["linked"].map_number) == ("123", 1)
    assert by_id["linked"].data == games["linked"] and by_id["linked"].link["confidence"] == 0.99
    assert by_id["linked"].data["roundResults"] == games["linked"]["roundResults"]
    assert all(
        row.vlr_match_id is None and row.map_number is None and row.link is None and row.linked_at is None
        for riot_id, row in by_id.items()
        if riot_id != "linked"
    )
    assert by_id["no-vlr-map"].data == games["no-vlr-map"]
    assert by_id["unknown-map"].data == games["unknown-map"]

    # VLR scores the next map as Summit 13-7, so the stored game links on a later run.
    detail.data.append(
        MatchData(
            number=6,
            map="Summit",
            teams=[Team(name="Beta", score=13), Team(name="Alpha", score=7)],
            members=beta + alpha,
            rounds=[],
        )
    )
    # Riot now returns the empty game's rounds, so this run fetches and archives it.
    pages["matches/empty"] = game("Split", 13, 1, 300)
    with patch("httpx2.AsyncClient.get", side_effect=get):
        await riot_matches.riot_matches_cron({"redis": redis})
        assert fetch.await_count == 2
        assert requested.count("matches/empty") == 2

    async with sessions() as session:
        stored = list((await session.execute(select(RiotMatch))).scalars())
    by_id = {row.riot_id: row for row in stored}
    # Re-running neither duplicates a game nor re-links one already linked.
    assert len(stored) == len(games)
    assert by_id["empty"].data == pages["matches/empty"]
    assert (by_id["empty"].vlr_match_id, by_id["empty"].map_number) == (None, None)
    assert (by_id["no-vlr-map"].vlr_match_id, by_id["no-vlr-map"].map_number) == ("123", 6)
    assert by_id["no-vlr-map"].link["confidence"] == 0.99 and by_id["no-vlr-map"].linked_at is not None
    assert by_id["no-vlr-map"].data == games["no-vlr-map"]
    assert all(row.vlr_match_id is None for riot_id, row in by_id.items() if riot_id not in {"linked", "no-vlr-map"})
    # The model saw each fitting map's teams in panel order with their scoreboard players; 13-7 fit none
    # on the first run. The later run asked only about the newly scored map: turned-down and contested
    # games are remembered.
    asked = [call.args[1] for call in decide.await_args_list]
    assert [candidate.map_number for candidate in asked] == [1, 2, 3, 3, 6]
    remembered = {key.removeprefix("valesports:riot:rejected:") for key in values if key != "matches"}
    assert remembered == {"unsure", "voided", "replay"}
    assert asked[0].team1 == VlrTeam(name="Beta", tag="BET", map_score=13, players=[f"b{i}" for i in range(5)])

    # A 429 from Riot ends the run before the next game is requested, so the key isn't hammered.
    pages["recent-matches/by-queue/tournamentmode"] = {"matchIds": ["throttled", "after-throttle"]}
    pages |= {"matches/throttled": 429, "matches/after-throttle": games["linked"]}
    requested.clear()
    with patch("httpx2.AsyncClient.get", side_effect=get):
        await riot_matches.riot_matches_cron({"redis": redis})
    assert requested == ["recent-matches/by-queue/tournamentmode", "matches/throttled"]
    # A refused game list, such as an expired key, is logged, not raised every run.
    pages["recent-matches/by-queue/tournamentmode"] = 401
    with patch("httpx2.AsyncClient.get", side_effect=get):
        await riot_matches.riot_matches_cron({"redis": redis})

    # Without an LLM key the archive still runs; only linking is skipped.
    pages["recent-matches/by-queue/tournamentmode"] = {"matchIds": ["archive-only"]}
    pages["matches/archive-only"] = game("Jam", 13, 9, 400)
    monkeypatch.setattr(riot_matches.settings, "LLM_API_KEY", None)
    decide.reset_mock()
    with patch("httpx2.AsyncClient.get", side_effect=get):
        await riot_matches.riot_matches_cron({"redis": redis})
    decide.assert_not_awaited()
    async with sessions() as session:
        archived = (await session.execute(select(RiotMatch).where(RiotMatch.riot_id == "archive-only"))).scalar_one()
    assert (archived.vlr_match_id, archived.map_number, archived.link) == (None, None, None)


def _riot_web_event(riot_id, teams, start, *, league="VCT Americas", slug="vct_americas", state="completed"):
    return {
        "startTime": start.isoformat(),
        "state": state,
        "type": "match",
        "blockName": "Week 1",
        "league": {"name": league, "slug": slug},
        "tournament": {"split": {"name": "Stage 2"}, "season": {"name": "valorant_champions_tour_2026"}},
        "match": {
            "id": riot_id,
            "teams": [
                {
                    "name": name,
                    "code": code,
                    "image": f"https://cdn.test/{code.lower()}.png",
                    "result": {"outcome": "win" if wins else "loss", "gameWins": wins},
                }
                for name, code, wins in teams
            ],
            "strategy": {"type": "bestOf", "count": 3},
        },
    }


def _riot_web_page(events, older=None):
    return {"data": {"schedule": {"events": events, "pages": {"older": older, "newer": None}}}}


def _vlr_listed(match_id, team1, team2, when, *, event="VCT 2026: Americas Stage 2"):
    return Match(
        id=match_id,
        team1=MatchTeam(name=team1),
        team2=MatchTeam(name=team2),
        status=MatchStatus.COMPLETED,
        time=when,
        event=event,
        series="Stage 2",
    )


def test_riot_web_link_scopes_gc_aliases_to_the_competition():
    """A main-roster series must not take a GC fixture or borrow its alias; GC still resolves MIBR GC."""
    from app.services.riot_web_link import RiotCandidate, link, team_keys

    start = datetime(2026, 10, 18, 6, tzinfo=UTC)
    assert "mibr" not in team_keys("Made In Brazil", False, riot=True)
    non_gc = RiotCandidate("115581239610581336", start, ("Made In Brazil", "Cloud9"), "VCT Americas")
    assert link([non_gc], [_vlr_listed("2001", "MIBR", "Cloud9", start)], set()) == []

    gc = RiotCandidate("115581239610581337", start, ("Made In Brazil", "Cloud9"), "Game Changers 2026: Brazil Stage 2")
    assert [
        (item.riot_id, item.vlr_match_id)
        for item in link(
            [gc],
            [_vlr_listed("2002", "MIBR GC", "Cloud9", start, event="Game Changers 2026: Brazil Stage 2")],
            set(),
        )
    ] == [("115581239610581337", "2002")]

    # A main-roster series must never take the GC fixture of the same orgs, or the reverse: both
    # sides strip their own "GC", so the competition kind, not only the keys, has to agree.
    main = RiotCandidate("115581239610581338", start, ("MIBR", "Cloud9"), "VCT Americas")
    gc_listing = [_vlr_listed("2003", "MIBR GC", "Cloud9 GC", start, event="Game Changers 2026: Brazil Stage 2")]
    assert link([main], gc_listing, set()) == []

    gc_riot = RiotCandidate("115581239610581339", start, ("MIBR", "Cloud9"), "Game Changers 2026: Brazil Stage 2")
    main_listing = [_vlr_listed("2004", "MIBR", "Cloud9", start)]
    assert link([gc_riot], main_listing, set()) == []

    # Same competition kind and the same normalised pair still links.
    assert [(item.riot_id, item.vlr_match_id) for item in link([main], main_listing, set())] == [
        ("115581239610581338", "2004")
    ]


def test_riot_web_reads_a_real_live_payload_and_links_it():
    """A captured live `getLiveDetails` payload parses, and its aliases link it to the VLR match."""
    from pathlib import Path

    from app.schemas.riot_web import ScheduleEnvelope, ScheduleEvent
    from app.services.riot_web_link import RiotCandidate, link

    fixture = Path(__file__).parent / "fixtures" / "riot_web" / "live_gc_championship_2026.json"
    payload = json.loads(fixture.read_text())
    event = ScheduleEvent.model_validate(ScheduleEnvelope.model_validate(payload).data.schedule.events[0])
    assert (event.match.id, [team.name for team in event.match.teams]) == (
        "117200188349540112",
        ["Gentle Mates", "Team Liquid VISA"],
    )

    # Riot says "Team Liquid VISA" and "Gentle Mates"; VLR says "Team Liquid Brazil" and "Gentle Mates GC".
    listed = _vlr_listed(
        "773430",
        "Team Liquid Brazil",
        "Gentle Mates GC",
        event.start_time,
        event="Game Changers 2026: Brazil Stage 2",
    )
    candidate = RiotCandidate(
        event.match.id,
        event.start_time,
        tuple(team.name for team in event.match.teams),
        event.league.name,
    )
    links = link([candidate], [listed], set())
    assert [(item.riot_id, item.vlr_match_id) for item in links] == [("117200188349540112", "773430")]


@pytest.mark.asyncio
async def test_riot_web_schedule_cron_links_each_riot_match_to_one_vlr_match(monkeypatch, caplog, make_database):
    """Name pair plus time links one VLR match; ambiguity, TBD and a claimed id stay unlinked."""
    import httpx2
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import schemas
    from app.core import connections
    from app.cron import riot_web as riot_web_cron
    from app.db.migrations import upgrade_to_head
    from app.db.models import RiotWebMatch
    from app.services import riot_web

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(riot_web.settings, "RIOT_WEB_API_KEY", "test-key")

    now = datetime.now(UTC).replace(microsecond=0)
    base = now + timedelta(hours=2)
    exact, suffix, fallback, alias, gc, mibr_gc, tbd, ambiguous, claimed, page_two = (
        str(115581239610581334 + offset) for offset in range(10)
    )
    first_page = [
        None,  # a null entry must cost only itself, never the page
        _riot_web_event(exact, [("100 Thieves", "100T", 3), ("LOUD", "LOUD", 2)], base),
        _riot_web_event(suffix, [("TYLOO GAMING", "TYL", 2), ("FNATIC", "FNC", 1)], base + timedelta(hours=1)),
        _riot_web_event(fallback, [("FOKUS Clan", "FOK", 2), ("Team Heretics", "TH", 1)], base + timedelta(hours=3)),
        _riot_web_event(alias, [("Fluxo", "FLX", 2), ("MIBR", "MIBR", 1)], base + timedelta(hours=4)),
        _riot_web_event(
            gc,
            [("Gentle Mates", "GM", 2), ("G2 Esports", "G2", 1)],
            base + timedelta(hours=5),
            league="Game Changers Brazil",
            slug="game_changers_series_brazil",
        ),
        _riot_web_event(mibr_gc, [("MIBR", "MIBR", 2), ("Cloud9", "C9", 1)], base + timedelta(hours=6)),
        _riot_web_event(tbd, [("TBD", "TBD", 0), ("Sentinels", "SEN", 0)], base + timedelta(hours=7)),
        _riot_web_event(ambiguous, [("Ambiguous Esports", "AMB", 2), ("Rivals", "RIV", 1)], base + timedelta(hours=8)),
        _riot_web_event(claimed, [("Taken", "TAK", 2), ("Other", "OTH", 1)], base + timedelta(hours=9)),
    ]
    cursor = "b2xkZXI6OjExNTU4MTIzOTYxMDU4MTMzNA=="
    pages = {
        None: _riot_web_page(first_page, cursor),
        cursor: _riot_web_page(
            [_riot_web_event(page_two, [("Page Two", "P2", 2), ("Second Team", "SEC", 1)], base + timedelta(hours=10))]
        ),
    }
    listing = [
        _vlr_listed("1001", "100 Thieves", "LOUD", base),
        _vlr_listed("1002", "TYLOO", "FNATIC", base + timedelta(hours=2, minutes=30)),
        _vlr_listed("1003", "FOKUS", "Team Heretics", base + timedelta(hours=6)),
        _vlr_listed("1004", "LA MASIA", "MIBR", base + timedelta(hours=4)),
        _vlr_listed(
            "1005", "Gentle Mates GC", "G2", base + timedelta(hours=5), event="Game Changers 2026: Brazil Stage 2"
        ),
        _vlr_listed("1006", "Taken", "Other", base + timedelta(hours=9)),
        _vlr_listed("1007", "MIBR GC", "Cloud9", base + timedelta(hours=6)),
        _vlr_listed("1008", "Ambiguous", "Rivals", base + timedelta(hours=8)),
        _vlr_listed("1009", "Ambiguous", "Rivals", base + timedelta(hours=8, minutes=30)),
        _vlr_listed("1010", "TBD", "Sentinels", base + timedelta(hours=7)),
        _vlr_listed("1011", "Page Two", "Second Team", base + timedelta(hours=10)),
    ]

    async with sessions.begin() as session:
        session.add(
            RiotWebMatch(
                riot_id="900000000000000001",
                start_time=base + timedelta(hours=9),
                league_name="VCT Americas",
                teams=[{"name": "Taken"}, {"name": "Other"}],
                vlr_match_id="1006",
            )
        )

    class FakeRedis:
        def __init__(self):
            self.values = {"matches": schemas.MatchListAdapter.dump_json(listing)}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            if nx and key in self.values:
                return None
            self.values[key] = value
            return True

    redis = FakeRedis()
    requested = []

    async def router(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        requested.append(request)
        return httpx2.Response(200, json=pages[(params or {}).get("pageToken")], request=request)

    caplog.set_level(logging.WARNING)
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        await riot_web_cron.riot_web_schedule_cron({"redis": redis})

    async def stored():
        async with sessions() as session:
            rows = (await session.execute(select(RiotWebMatch).order_by(RiotWebMatch.riot_id))).scalars()
            return {row.riot_id: row.vlr_match_id for row in rows}

    rows = await stored()
    assert rows == {
        exact: "1001",
        suffix: "1002",
        fallback: "1003",
        alias: "1004",
        gc: "1005",
        mibr_gc: None,
        tbd: None,
        ambiguous: None,
        claimed: None,
        page_two: "1011",
        "900000000000000001": "1006",
    }
    assert any("fits 2 VLR matches" in record.message and ambiguous in record.message for record in caplog.records)
    assert [record.message for record in caplog.records].count("skipping an unreadable Riot web schedule event") == 1
    # The cursor, never page=, and the params and key every request must carry.
    assert [request.url.params.get("pageToken") for request in requested] == [None, cursor]
    assert all(request.url.path == "/persisted/val/getSchedule" for request in requested)
    assert all(request.url.params.get("sport") == "val" for request in requested)
    assert all(request.url.params.get("hl") == "en-US" for request in requested)
    assert all(request.headers["x-api-key"] == "test-key" for request in requested)

    # A rejected key changes nothing and alerts once; the next run retries.
    before = await stored()
    caplog.clear()

    async def forbidden(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        return httpx2.Response(403, json={"message": "Forbidden"}, request=request)

    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=forbidden):
        await riot_web_cron.riot_web_schedule_cron({"redis": redis})
    assert [record.message for record in caplog.records if record.levelno == logging.ERROR] == [
        "Riot web rejected RIOT_WEB_API_KEY (HTTP 403); rotate it, see docs/cron.md"
    ]
    assert await stored() == before

    # Without the key no Riot call is made: the key is the whole switch.
    monkeypatch.setattr(riot_web.settings, "RIOT_WEB_API_KEY", None)
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router) as get:
        await riot_web_cron.riot_web_schedule_cron({"redis": redis})
    get.assert_not_called()


@pytest.mark.asyncio
async def test_riot_web_save_links_keeps_the_batch_when_two_writers_pick_one_vlr_match(make_database):
    """A unique violation from a concurrent claim skips that link only, keeping the rest of the batch."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.migrations import upgrade_to_head
    from app.db.models import RiotWebMatch
    from app.services import riot_web_store
    from app.services.riot_web_link import Link

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)

    def row(riot_id, name):
        return RiotWebMatch(
            riot_id=riot_id,
            start_time=now,
            league_name="VCT Americas",
            teams=[{"name": name}, {"name": "Rival"}],
        )

    first, second, other = "115581239610581401", "115581239610581402", "115581239610581403"
    async with sessions.begin() as session:
        session.add_all([row(first, "Alpha"), row(second, "Beta"), row(other, "Gamma")])

    try:
        async with sessions.begin() as session:
            written = await riot_web_store.save_links(
                session,
                [
                    Link(first, "5001", "9001", 0),
                    Link(second, "5001", "9001", 0),
                    Link(other, "5002", "9002", 0),
                ],
            )
        assert written == 2
        async with sessions() as session:
            linked = {item.riot_id: item.vlr_match_id for item in (await session.scalars(select(RiotWebMatch))).all()}
        assert linked == {first: "5001", second: None, other: "5002"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_riot_web_schedule_cron_flags_times_that_disagree_with_every_name_match(
    monkeypatch, caplog, make_database
):
    """Three name pairs and no delta inside two hours means the host timezone, not the teams, is wrong."""
    import httpx2
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import schemas
    from app.core import connections
    from app.cron import riot_web as riot_web_cron
    from app.db.migrations import upgrade_to_head
    from app.db.models import RiotWebMatch
    from app.services import riot_web

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(riot_web.settings, "RIOT_WEB_API_KEY", "test-key")

    base = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    riot_ids = [str(200000000000000000 + offset) for offset in range(3)]
    events = [
        _riot_web_event(
            riot_id,
            [(f"Shift Team {offset}", f"SH{offset}", 1), ("Their Rivals", "RIV", 0)],
            base + timedelta(hours=offset),
        )
        for offset, riot_id in enumerate(riot_ids)
    ]
    listing = [
        _vlr_listed(f"200{offset}", f"Shift Team {offset}", "Their Rivals", base + timedelta(hours=offset, minutes=420))
        for offset in range(3)
    ]
    pages = {None: _riot_web_page(events)}

    class FakeRedis:
        async def get(self, key):
            return schemas.MatchListAdapter.dump_json(listing) if key == "matches" else None

        async def set(self, key, value, *, nx=False, ex=None):
            return True

    async def router(client, url, *, params=None, **_kwargs):
        return httpx2.Response(200, json=pages[None], request=client.build_request("GET", url, params=params))

    caplog.set_level(logging.WARNING)
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        await riot_web_cron.riot_web_schedule_cron({"redis": FakeRedis()})

    assert any(record.levelno == logging.ERROR and "times disagree" in record.message for record in caplog.records)
    async with sessions() as session:
        rows = (await session.execute(select(RiotWebMatch))).scalars().all()
    assert len(rows) == 3 and all(row.vlr_match_id is None for row in rows)


@pytest.mark.asyncio
async def test_live_push_cron_follows_riot_live_matches_without_ending_or_refinalizing(
    monkeypatch, caplog, make_database
):
    """Riot only adds VLR ids to read: the VLR detail still decides every push, ending and final."""
    import httpx2
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core import connections
    from app.db.migrations import upgrade_to_head
    from app.db.models import RiotWebMatch
    from app.services import live_store, push
    from app.services.subscription_store import SubscriptionStore

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    riot_id, match_id = "115581239610581334", "123"
    now = datetime.now(UTC).replace(microsecond=0)
    android_id = "22222222-2222-4222-8222-222222222222"
    async with sessions.begin() as session:
        store = SubscriptionStore(session)
        await store.register_token(android_id, "fcm-token:APA91b", Platform.ANDROID)
        await store.add_favorites(android_id, Favorites(matches=[match_id]))
        session.add(
            RiotWebMatch(
                riot_id=riot_id,
                start_time=now,
                league_name="Game Changers 2026: Championship",
                teams=[{"name": "Alpha"}, {"name": "Beta"}],
                vlr_match_id=match_id,
            )
        )

    outcome = [_live_detail("live", (1, 0))]
    fetched = []

    async def fetch_detail(client, fetched_id):
        fetched.append(fetched_id)
        result = outcome[0]
        if isinstance(result, BaseException):
            raise result
        return result

    sent = []

    async def publish_direct(app, tokens, state):
        sent.append(state)
        return [f"message:{len(sent)}"]

    requested = []
    riot_failure = [False]

    async def live_router(client, url, *, params=None, **kwargs):
        request = client.build_request("GET", url, params=params)
        requested.append((request, kwargs.get("timeout")))
        if riot_failure[0]:
            return httpx2.Response(503, json={}, request=request)
        event = _riot_web_event(
            riot_id,
            [("Alpha", "ALP", 1), ("Beta", "BET", 0)],
            now,
            league="Game Changers 2026: Championship",
            slug="game_changers",
            state="inProgress",
        )
        return httpx2.Response(200, json={"data": {"schedule": {"events": [event]}}}, request=request)

    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            if nx and key in self.values:
                return None
            self.values[key] = value if isinstance(value, bytes) else str(value).encode()
            return True

        async def delete(self, *keys):
            for key in keys:
                self.values.pop(key, None)
            return True

        async def incr(self, key):
            self.values[key] = str(int(self.values.get(key, b"0")) + 1).encode()
            return int(self.values[key])

        async def expire(self, key, ttl):
            return True

    redis = FakeRedis()
    monkeypatch.setattr(live_push.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(live_push.settings, "GOOGLE_APPLICATION_CREDENTIALS", "configured")
    monkeypatch.setattr(live_push.settings, "RIOT_WEB_API_KEY", "test-key")
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(connections, "apns_client", None)
    monkeypatch.setattr(push, "video_score", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_match_left_to_video", AsyncMock(return_value=None))
    monkeypatch.setattr(live_push, "_listed_live_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(live_push, "_fetch_detail", fetch_detail)
    monkeypatch.setattr(live_push.fcm, "get_app", lambda: object())
    monkeypatch.setattr(live_push.fcm, "publish_direct", publish_direct)
    caplog.set_level(logging.INFO)

    async def run():
        with patch("httpx2.AsyncClient.get", autospec=True, side_effect=live_router):
            await live_push.live_push_cron({"redis": redis})

    try:
        await run()

        # Riot's listing found the match VLR's did not; VLR's detail still supplied the push.
        assert fetched == [match_id]
        assert [(state.match_id, state.terminal) for state in sent] == [(match_id, False)]
        async with sessions() as session:
            assert await SubscriptionStore(session).get_match(match_id) is not None
        assert any("Riot lists match 123 live; VLR's listing does not" in r.message for r in caplog.records)
        async with sessions() as session:
            assert await live_store.get_live_match(session, match_id) is not None
        request, timeout = requested[0]
        assert request.url.path == "/persisted/val/getLiveDetails"
        assert request.url.params.get("sport") == "val"
        assert request.url.params.get("hl") == "en-US"
        assert request.headers["x-api-key"] == "test-key"
        assert timeout == constants.RIOT_WEB_LIVE_TIMEOUT

        # Riot's list lingers after the series ends; a Riot-only final with no push state must not resend it.
        async with sessions.begin() as session:
            await SubscriptionStore(session).delete_match(match_id)
        outcome[0] = _live_detail("completed", (1, 0))
        caplog.clear()
        await run()
        assert len(sent) == 1
        async with sessions() as session:
            assert await SubscriptionStore(session).get_match(match_id) is None
            # The final detail the cache stored must not leave a live row for a series with no push state.
            assert await live_store.get_live_match(session, match_id) is None
        # The answer was cached, so the minute's single Riot call was reused.
        assert len(requested) == 1

        # A Riot-live id whose VLR detail keeps failing is not a match Riot may end.
        async with sessions.begin() as session:
            await SubscriptionStore(session).save_match(match_id, None, None)
        outcome[0] = ScrapingError(upstream_status=502)
        ended = AsyncMock()
        monkeypatch.setattr(live_push, "_end_unavailable_match", ended)
        for _ in range(constants.PUSH_FETCH_FAILURE_LIMIT + 1):
            await run()
        ended.assert_not_awaited()
        async with sessions() as session:
            assert await SubscriptionStore(session).get_match(match_id) is not None

        # Riot's lingering list defers that failure only for the grace window: past it the normal
        # path decides, so a match whose page keeps failing is still cleaned up.
        redis.values[constants.PUSH_RIOT_LIVE_SINCE_KEY.format(match_id)] = str(
            int(time.time()) - constants.PUSH_RIOT_LIVE_GRACE_SECONDS - 1
        ).encode()
        for _ in range(constants.PUSH_FETCH_FAILURE_LIMIT):
            await run()
        ended.assert_awaited()

        # Riot failing leaves today's VLR-only behaviour untouched.
        async with sessions.begin() as session:
            await SubscriptionStore(session).delete_match(match_id)
        redis.values.pop(constants.RIOT_WEB_LIVE_KEY, None)
        outcome[0] = _live_detail("live", (1, 0))
        monkeypatch.setattr(live_push, "_listed_live_ids", AsyncMock(return_value={match_id}))
        riot_failure[0] = True
        caplog.clear()
        await run()
        assert len(sent) == 2
        assert any("could not read Riot's live list" in r.message for r in caplog.records)

        # A cached list mixing ids with another type is a miss, not ids that break the cron's sorting.
        async with sessions.begin() as session:
            await SubscriptionStore(session).delete_match(match_id)
        redis.values[constants.RIOT_WEB_LIVE_KEY] = json.dumps([match_id, 456]).encode()
        outcome[0] = _live_detail("live", (1, 0))
        riot_failure[0] = False
        requests_before = len(requested)
        caplog.clear()
        await run()
        assert len(sent) == 3
        assert len(requested) == requests_before + 1
        assert json.loads(redis.values[constants.RIOT_WEB_LIVE_KEY]) == [match_id]
        assert any("not a list of VLR match ids" in r.message for r in caplog.records)

        # An unexpected discovery failure must not escape gather and silence the VLR pushes.
        async with sessions.begin() as session:
            await SubscriptionStore(session).delete_match(match_id)
        redis.values.pop(constants.RIOT_WEB_LIVE_KEY, None)
        outcome[0] = _live_detail("live", (1, 0))
        monkeypatch.setattr(
            live_push.riot_web_store, "vlr_ids_for", AsyncMock(side_effect=RuntimeError("discovery bug"))
        )
        caplog.clear()
        await run()
        assert len(sent) == 4
        assert any("could not use Riot's live list" in r.message for r in caplog.records)

        # With the key unset, no Riot request is made and the VLR path is unchanged.
        async with sessions.begin() as session:
            await SubscriptionStore(session).delete_match(match_id)
        redis.values.pop(constants.RIOT_WEB_LIVE_KEY, None)
        monkeypatch.setattr(live_push.settings, "RIOT_WEB_API_KEY", None)
        requests_before = len(requested)
        await run()
        assert len(sent) == 5
        assert len(requested) == requests_before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_riot_live_discovery_links_only_rows_it_persisted(monkeypatch, make_database):
    """A new live id gets its row before linking, a durable link beats a stale proposal, and guessed metadata never links."""
    import httpx2
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import schemas
    from app.db.migrations import upgrade_to_head
    from app.db.models import RiotWebMatch
    from app.services import riot_web_store

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(live_push.settings, "RIOT_WEB_API_KEY", "test-key")

    now = datetime.now(UTC).replace(microsecond=0)
    riot_id, vlr_id = "115581239610581399", "4321"

    class FakeRedis:
        def __init__(self, matches):
            self.values = {"matches": schemas.MatchListAdapter.dump_json(matches)}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            self.values[key] = value if isinstance(value, bytes) else str(value).encode()
            return True

    async def discover(redis, event):
        async def router(client, url, *, params=None, **_kwargs):
            request = client.build_request("GET", url, params=params)
            return httpx2.Response(200, json={"data": {"schedule": {"events": [event]}}}, request=request)

        with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
            return await live_push._riot_live_ids(redis, sessions)

    try:
        # A live series the schedule has not stored yet needs its row inserted before the link can persist.
        redis = FakeRedis([_vlr_listed(vlr_id, "Gamma", "Delta", now)])
        event = _riot_web_event(riot_id, [("Gamma", "GAM", 1), ("Delta", "DEL", 0)], now, state="inProgress")
        assert await discover(redis, event) == {vlr_id}
        assert json.loads(redis.values[constants.RIOT_WEB_LIVE_KEY]) == [vlr_id]
        async with sessions() as session:
            row = await session.get(RiotWebMatch, riot_id)
        assert row is not None
        assert (row.vlr_match_id, row.start_time, row.league_name) == (vlr_id, now, "VCT Americas")
        assert [team["name"] for team in row.teams] == ["Gamma", "Delta"]

        # Missing a real start time or competition metadata is not the measured rule, so no row or link may appear.
        redis = FakeRedis([_vlr_listed("2001", "Missing", "Metadata", now)])
        for index, field in enumerate(("startTime", "league")):
            bad_riot_id = f"1155812396105814{index:02d}"
            bad_event = _riot_web_event(
                bad_riot_id, [("Missing", "MIS", 0), ("Metadata", "MET", 0)], now, state="inProgress"
            )
            bad_event.pop(field)
            redis.values.pop(constants.RIOT_WEB_LIVE_KEY, None)
            assert await discover(redis, bad_event) == set()
            async with sessions() as session:
                assert await session.get(RiotWebMatch, bad_riot_id) is None
            assert json.loads(redis.values[constants.RIOT_WEB_LIVE_KEY]) == []

        # The stricter rule governs new links only: a durably linked series still rides along without metadata.
        lenient_riot_id, lenient_vlr_id = "115581239610581403", "3001"
        async with sessions.begin() as session:
            session.add(
                RiotWebMatch(
                    riot_id=lenient_riot_id,
                    start_time=now,
                    league_name="VCT Americas",
                    teams=[{"name": "Known"}, {"name": "Linked"}],
                    vlr_match_id=lenient_vlr_id,
                )
            )
        lenient_event = _riot_web_event(
            lenient_riot_id, [("Known", "KNW", 1), ("Linked", "LNK", 0)], now, state="inProgress"
        )
        for field in ("startTime", "league"):
            lenient_event.pop(field)
        redis.values.pop(constants.RIOT_WEB_LIVE_KEY, None)
        assert await discover(redis, lenient_event) == {lenient_vlr_id}
        assert json.loads(redis.values[constants.RIOT_WEB_LIVE_KEY]) == [lenient_vlr_id]

        # A durable link committed after discovery's first read still wins over the stale proposal.
        stale_riot_id, durable_id, proposed_id = "115581239610581402", "9999", "1001"
        established_start = now - timedelta(hours=2)
        redis = FakeRedis([_vlr_listed(proposed_id, "Racer", "Stale", now)])
        async with sessions.begin() as session:
            session.add(
                RiotWebMatch(
                    riot_id=stale_riot_id,
                    start_time=established_start,
                    league_name="Established League",
                    teams=[{"name": "Racer"}, {"name": "Stale"}],
                    vlr_match_id=durable_id,
                )
            )
        event = _riot_web_event(stale_riot_id, [("Racer", "RAC", 1), ("Stale", "STA", 0)], now, state="inProgress")
        real_vlr_ids_for = riot_web_store.vlr_ids_for
        reads = 0

        async def stale_first_read(session, riot_ids):
            nonlocal reads
            reads += 1
            return {} if reads == 1 else await real_vlr_ids_for(session, riot_ids)

        monkeypatch.setattr(riot_web_store, "vlr_ids_for", stale_first_read)
        assert await discover(redis, event) == {durable_id}
        assert reads == 2
        assert json.loads(redis.values[constants.RIOT_WEB_LIVE_KEY]) == [durable_id]
        async with sessions() as session:
            row = await session.get(RiotWebMatch, stale_riot_id)
        assert (row.vlr_match_id, row.start_time, row.league_name) == (
            durable_id,
            established_start,
            "Established League",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_riot_matches_downgrade_refuses_to_lose_unlinked_archive(make_database):
    """A downgrade aborts while an unlinked game exists, and restores the old schema once every row is linked."""
    import sqlalchemy as sa
    from alembic.config import Config
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command
    from app.db.migrations import upgrade_to_head

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)

    def downgrade() -> None:
        config = Config("alembic.ini")
        config.attributes["app"] = True
        config.set_main_option("sqlalchemy.url", database_url)
        # The Riot web and archive changes are one revision, so "-1" leaves exactly its predecessor.
        command.downgrade(config, "-1")

    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    """INSERT INTO riot_matches (riot_id, started_at, data)
                    VALUES ('unlinked', now(), '{"roundResults": [{"roundNum": 1}]}'::jsonb)"""
                )
            )

        with pytest.raises(RuntimeError, match="1 riot_matches rows are unlinked or partial"):
            await asyncio.to_thread(downgrade)

        # The refused downgrade leaves the archive row in place instead of deleting it.
        async with engine.connect() as connection:
            assert await connection.scalar(sa.text("SELECT count(*) FROM riot_matches")) == 1

        async with engine.begin() as connection:
            await connection.execute(sa.text("DELETE FROM riot_matches"))
            await connection.execute(
                sa.text(
                    "INSERT INTO riot_matches "
                    "(riot_id, vlr_match_id, map_number, started_at, linked_at, data, link) "
                    "VALUES ('linked', '123', 1, now(), now(), '{}'::jsonb, '{}'::jsonb)"
                )
            )

        await asyncio.to_thread(downgrade)

        async with engine.connect() as connection:
            columns = {
                row.column_name: row.is_nullable
                for row in (
                    await connection.execute(
                        sa.text(
                            "SELECT column_name, is_nullable FROM information_schema.columns "
                            "WHERE table_name = 'riot_matches'"
                        )
                    )
                ).all()
            }
            constraints = set(
                await connection.scalars(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = 'riot_matches'::regclass AND contype = 'u'"
                    )
                )
            )
            assert await connection.scalar(sa.text("SELECT count(*) FROM riot_matches")) == 1

        assert {name: columns[name] for name in ("vlr_match_id", "map_number", "linked_at", "link")} == {
            "vlr_match_id": "NO",
            "map_number": "NO",
            "linked_at": "NO",
            "link": "NO",
        }
        assert constraints == {"riot_matches_vlr_match_id_map_number_key"}
    finally:
        await engine.dispose()
