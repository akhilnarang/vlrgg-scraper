from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import constants
from app.exceptions import ScrapingError
from app.services import matches

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _live_snapshot(match_id, version, status, series, maps):
    """Build one full snapshot for the SSE stream tests."""
    from datetime import UTC, datetime

    from app.schemas.matches import Event, LiveSnapshot, MatchVideos, MatchWithDetails, TeamWithImage

    detail = MatchWithDetails(
        teams=[
            TeamWithImage(id="1", name="Alpha", score=series[0], img="https://cdn.vlr.gg/a.png"),
            TeamWithImage(id="2", name="Beta", score=series[1], img="https://cdn.vlr.gg/b.png"),
        ],
        bans=[],
        event=Event(id="2283", img="https://cdn.vlr.gg/e.png", series="Series", stage="Group", status=status),
        videos=MatchVideos(streams=[], vods=[]),
        map_count=len(maps),
        data=maps,
        previous_encounters=[],
    )
    return LiveSnapshot(match_id=match_id, version=version, observed_at=datetime(2026, 1, 1, tzinfo=UTC), data=detail)


@pytest.mark.asyncio
async def test_match_details_follow_the_public_response_contract(http_response):
    response = http_response("https://www.vlr.gg/12345", (FIXTURE_DIR / "match_12345.html").read_bytes())

    with patch("httpx.AsyncClient.get", return_value=response):
        result = await matches.match_by_id("12345", AsyncMock())

    assert [(team.name, team.score) for team in result.teams] == [("Team A", 2), ("Team B", 1)]
    assert result.event.id == "2283"
    assert result.event.series == "Event Series"
    assert result.map_count == 1
    assert [(item.map, [team.score for team in item.teams]) for item in result.data] == [("Lotus", [13, 10])]
    member = result.data[0].members[0]
    assert (member.id, member.name, member.team) == ("2114", "Kinguyen", "Team A")
    assert (member.agents[0].title, member.rating, member.kills) == ("Raze", 1.42, 29)
    # Fixture note isn't a per-step veto, so it must surface as unknown with the raw text, never be dropped.
    assert [(v.team, v.action, v.map) for v in result.veto] == [(None, "unknown", "Map ban: Bind, Haven")]
    assert [
        (v.team, v.action, v.map)
        for v in matches.parse_veto(["Paper Rex ban Bind", "FNC pick Lotus", "Sunset remains"])
    ] == [
        ("Paper Rex", "ban", "Bind"),
        ("FNC", "pick", "Lotus"),
        (None, "remains", "Sunset"),
    ]


def test_display_strings_follow_accept_language(http_response):
    """Localized labels come from the device locale header; no header keeps the legacy English payload."""
    from fastapi import FastAPI
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.testclient import TestClient

    from app import i18n
    from app.api.v1.endpoints.matches import router

    # Mirror production: localization registers first, GZip registers last and is outermost.
    app = FastAPI()
    app.middleware("http")(i18n.localize_response)
    app.add_middleware(GZipMiddleware, minimum_size=0)
    app.include_router(router, prefix="/api/v1/matches")

    response = http_response("https://www.vlr.gg/12345", (FIXTURE_DIR / "match_12345.html").read_bytes())
    with (
        patch("httpx.AsyncClient.get", return_value=response),
        patch("app.services.matches.get_team_data", AsyncMock(return_value=[])),
    ):
        client = TestClient(app)
        english = client.get("/api/v1/matches/12345")
        hindi = client.get("/api/v1/matches/12345", headers={"Accept-Language": "hi-IN,hi;q=0.9,en;q=0.8"})
        unknown = client.get("/api/v1/matches/12345", headers={"Accept-Language": "xx"})

    assert english.status_code == hindi.status_code == 200
    assert english.headers["content-language"] == "en" and hindi.headers["content-language"] == "hi"
    en_event, hi_event = english.json()["event"], hindi.json()["event"]
    assert (en_event["status"], en_event["status_label"]) == ("completed", "Completed")
    assert (hi_event["status"], hi_event["status_label"]) == ("completed", "समाप्त")  # existing field never changes
    assert (english.json()["veto"][0]["action_label"], hindi.json()["veto"][0]["action_label"]) == ("Unknown", "अज्ञात")
    assert unknown.json() == english.json() and unknown.headers["content-language"] == "en"

    assert i18n.resolve("pt") == "pt-BR" and i18n.resolve("de;q=0.5, ko;q=0.8") == "ko"
    assert i18n.resolve("de;q=0, ko") == "ko"  # q=0 means not acceptable
    assert i18n.resolve("de ;Q=0.1, ko;q=0.5") == "ko" and i18n.resolve("de;q=2, ko;q=1") == "ko"  # lenient syntax
    vary_tokens = {token.strip().lower() for token in unknown.headers["vary"].split(",")}
    assert vary_tokens == {"accept-encoding", "accept-language"}  # both Vary tokens survive, order is not fixed


@pytest.mark.asyncio
async def test_match_list_keeps_each_upcoming_date_group(monkeypatch, http_response):
    responses = {
        constants.UPCOMING_MATCHES_URL: http_response(
            constants.UPCOMING_MATCHES_URL, (FIXTURE_DIR / "matches.html").read_bytes()
        ),
        constants.PAST_MATCHES_URL: http_response(
            constants.PAST_MATCHES_URL, (FIXTURE_DIR / "matches_results.html").read_bytes()
        ),
    }
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)

    with patch("httpx.AsyncClient.get", side_effect=lambda url, *_args, **_kwargs: responses[url]):
        result = await matches.match_list(AsyncMock())

    names = {match.id: (match.team1.name, match.team2.name) for match in result}
    assert names["684611"] == ("FULL SENSE", "FUT Esports")
    assert names["684612"] == ("LEVIATÁN", "Global Esports")
    assert names["684610"] == ("Team Vitality", "Dragon Ranger Gaming")


@pytest.mark.asyncio
async def test_completed_matches_clamp_pages_and_keep_results_in_order(monkeypatch, http_get):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    pages = {
        constants.PAST_MATCHES_URL: (FIXTURE_DIR / "matches_results_page1.html").read_bytes(),
        matches.completed_matches_url(2): (FIXTURE_DIR / "matches_results_page2.html").read_bytes(),
    }

    with patch("httpx.AsyncClient.get", side_effect=http_get(pages)) as get:
        result = await matches.get_completed_matches(AsyncMock(), pages=9999)

    assert len(result) == 100
    assert len({match.id for match in result}) == 100
    assert result[0].id == "670476"
    assert result[50].id == "684615"
    assert get.call_count <= constants.MAX_PAGINATION_PAGES


@pytest.mark.asyncio
async def test_completed_matches_do_not_return_partial_results(monkeypatch, http_get):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAP_DB", False)
    pages = {constants.PAST_MATCHES_URL: (FIXTURE_DIR / "matches_results_page1.html").read_bytes()}
    failures = {matches.completed_matches_url(2): 502}

    with patch("httpx.AsyncClient.get", side_effect=http_get(pages, failures=failures)), pytest.raises(ScrapingError):
        await matches.get_completed_matches(AsyncMock(), pages=2)


def test_live_route_precedes_match_id_and_fails_closed_when_disabled(monkeypatch):
    """`/live` must never fall through to `/{id}`, and a disabled feature must not scrape."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints.matches import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/matches")
    monkeypatch.setattr("app.core.config.settings.ENABLE_LIVE_MATCHES", False, raising=False)
    monkeypatch.setattr("app.core.config.settings.ENABLE_CACHE", True, raising=False)

    with patch("app.services.matches.match_by_id", AsyncMock()) as match_by_id:
        response = TestClient(app).get("/api/v1/matches/live?match_id=123")

    assert response.status_code == 503
    assert response.json()["detail"] == "Live match streaming is not enabled"
    match_by_id.assert_not_awaited()


def test_live_admission_failure_does_not_scrape(monkeypatch, live_redis):
    """A Redis outage at admission fails closed with 503 instead of scraping on the request thread."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.v1.endpoints.matches import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/matches")
    app.dependency_overrides[deps.get_redis_client] = lambda: live_redis(fail_pipeline=True)
    monkeypatch.setattr("app.core.config.settings.ENABLE_LIVE_MATCHES", True, raising=False)
    monkeypatch.setattr("app.core.config.settings.ENABLE_CACHE", True, raising=False)

    with patch("app.services.matches.match_by_id", AsyncMock()) as match_by_id:
        response = TestClient(app).get("/api/v1/matches/live?match_id=123")

    assert response.status_code == 503
    assert response.json()["detail"] == "Live match state is unavailable"
    match_by_id.assert_not_awaited()


def test_live_stream_sends_compact_snapshot_then_end(monkeypatch, live_redis):
    """The stream sends compact scores with response caching off, aligns map scores, and ends per match."""
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.v1.endpoints.matches import router
    from app.schemas.matches import MatchData, Team

    in_progress = _live_snapshot(
        "123",
        1,
        "live",
        (0, 0),
        [
            MatchData(
                map="Haven", teams=[Team(name="Alpha", score=0), Team(name="Beta", score=0)], members=[], rounds=[]
            ),
            MatchData(
                map="Ascent", teams=[Team(name="Beta", score=4), Team(name="Alpha", score=5)], members=[], rounds=[]
            ),
        ],
    )
    # VLR reports a finished match as "final".
    final = _live_snapshot(
        "123",
        2,
        "final",
        (2, 0),
        [
            MatchData(
                map="Haven",
                teams=[Team(name="Alpha", score=13), Team(name="Beta", score=9)],
                members=[],
                rounds=[],
            ),
            MatchData(
                map="Ascent",
                teams=[Team(name="Beta", score=10), Team(name="Alpha", score=13)],
                members=[],
                rounds=[],
            ),
        ],
    )
    redis = live_redis(mget_script=[[in_progress.model_dump_json().encode()], [final.model_dump_json().encode()]])
    monkeypatch.setattr("app.core.config.settings.ENABLE_LIVE_MATCHES", True, raising=False)
    # Live coordination uses its own Redis keys, so it must not need response caching.
    monkeypatch.setattr("app.core.config.settings.ENABLE_CACHE", False, raising=False)
    monkeypatch.setattr(constants, "LIVE_POLL_INTERVAL", 0)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/matches")
    app.dependency_overrides[deps.get_redis_client] = lambda: redis

    with patch("httpx.AsyncClient.get", AsyncMock()) as scrape:
        response = TestClient(app).get("/api/v1/matches/live?match_id=00123&match_id=123")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    # "00123" and "123" canonicalize to one id, so one compact event per version and one composite id each.
    assert "event: snapshot" in response.text and "event: end" in response.text
    assert "id: 123:1" in response.text and "id: 123:2" in response.text
    payloads = [
        json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert len(payloads) == 2
    snapshot, end = payloads
    assert set(snapshot) == {"match_id", "version", "observed_at", "terminal", "teams", "current_map"}
    assert set(snapshot["teams"][0]) == {"name", "img", "score"}
    assert [team["img"] for team in snapshot["teams"]] == [
        "https://cdn.vlr.gg/a.png",
        "https://cdn.vlr.gg/b.png",
    ]
    assert snapshot["terminal"] is False
    assert [team["score"] for team in snapshot["teams"]] == [0, 0]
    # The 0-0 Haven map has not started, so the started Ascent map is current. Its card lists Beta first.
    assert snapshot["current_map"] == {"name": "Ascent", "scores": [5, 4]}
    assert end["terminal"] is True
    assert [team["score"] for team in end["teams"]] == [2, 0]
    assert end["current_map"] == {"name": "Ascent", "scores": [13, 10]}
    assert "maps" not in snapshot and "status" not in snapshot and "status_label" not in snapshot
    scrape.assert_not_awaited()  # the stream never scrapes VLR directly


def test_live_admission_rejects_invalid_id(monkeypatch, live_redis):
    """A non-ASCII-digit id gets a clear 400 and never scrapes VLR."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.v1.endpoints.matches import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/matches")
    app.dependency_overrides[deps.get_redis_client] = lambda: live_redis()
    monkeypatch.setattr("app.core.config.settings.ENABLE_LIVE_MATCHES", True, raising=False)

    with patch("app.services.matches.match_by_id", AsyncMock()) as match_by_id:
        response = TestClient(app).get("/api/v1/matches/live?match_id=bad-id")

    assert response.status_code == 400
    assert response.json()["detail"] == "match_id must be 1 to 10 ASCII digits"
    match_by_id.assert_not_awaited()


def test_live_stream_ends_one_match_and_keeps_reading_the_other(monkeypatch, live_redis):
    """An end event drops one match from reads while the other keeps streaming until Redis fails."""
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from redis.exceptions import RedisError

    from app.api import deps
    from app.api.v1.endpoints.matches import router
    from app.schemas.matches import MatchData, Team

    # The only eligible map is 0-0 with no rounds, so the projection falls back to it.
    opening = _live_snapshot(
        "111",
        1,
        "live",
        (0, 0),
        [
            MatchData(
                map="Haven", teams=[Team(name="Alpha", score=0), Team(name="Beta", score=0)], members=[], rounds=[]
            )
        ],
    )
    progressed = _live_snapshot(
        "111",
        2,
        "live",
        (1, 0),
        [
            MatchData(
                map="Haven", teams=[Team(name="Alpha", score=6), Team(name="Beta", score=4)], members=[], rounds=[]
            )
        ],
    )
    completed = _live_snapshot(
        "222",
        5,
        "completed",
        (2, 0),
        [
            MatchData(
                map="Ascent",
                teams=[Team(name="Beta", score=10), Team(name="Alpha", score=13)],
                members=[],
                rounds=[],
            )
        ],
    )
    redis = live_redis(
        mget_script=[
            [opening.model_dump_json().encode(), completed.model_dump_json().encode()],
            [progressed.model_dump_json().encode()],
            RedisError("redis unavailable"),
        ]
    )
    monkeypatch.setattr("app.core.config.settings.ENABLE_LIVE_MATCHES", True, raising=False)
    monkeypatch.setattr("app.core.config.settings.ENABLE_CACHE", True, raising=False)
    monkeypatch.setattr(constants, "LIVE_POLL_INTERVAL", 0)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/matches")
    app.dependency_overrides[deps.get_redis_client] = lambda: redis

    with patch("httpx.AsyncClient.get", AsyncMock()) as scrape:
        response = TestClient(app).get("/api/v1/matches/live?match_id=111&match_id=222")

    assert response.status_code == 200
    events = [tuple(line.split(": ", 1)) for line in response.text.splitlines() if line.startswith(("event: ", "id: "))]
    assert events == [
        ("event", "snapshot"),
        ("id", "111:1"),
        ("event", "end"),
        ("id", "222:5"),
        ("event", "snapshot"),
        ("id", "111:2"),
    ]
    payloads = [
        json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert [payload["match_id"] for payload in payloads] == ["111", "222", "111"]
    assert payloads[0]["terminal"] is False
    assert payloads[0]["current_map"] == {"name": "Haven", "scores": [0, 0]}  # 0-0 opening fallback
    assert payloads[1]["terminal"] is True
    assert [team["score"] for team in payloads[1]["teams"]] == [2, 0]
    assert payloads[1]["current_map"] == {"name": "Ascent", "scores": [13, 10]}
    assert payloads[2]["terminal"] is False and payloads[2]["current_map"]["scores"] == [6, 4]
    assert response.text.count("event: end") == 1  # END is per-match; the Redis failure sends no end
    scrape.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_stream_handles_early_disconnect_with_production_middleware(monkeypatch, live_redis):
    """An early disconnect before the first SSE body must not fail the production middleware stack."""
    import asyncio

    from app.api import deps
    from app.main import app
    from app.services import live

    class BlockingRedis(live_redis):
        """Fake Redis that keeps the first snapshot read pending."""

        def __init__(self):
            super().__init__()
            self.read_started = asyncio.Event()

        async def mget(self, keys):
            self.read_started.set()
            await asyncio.Event().wait()

    redis = BlockingRedis()
    monkeypatch.setattr("app.core.config.settings.ENABLE_LIVE_MATCHES", True, raising=False)
    monkeypatch.setattr("app.core.config.settings.ENABLE_CACHE", False, raising=False)

    sent = []
    disconnect = asyncio.Event()
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/matches/live",
        "raw_path": b"/api/v1/matches/live",
        "query_string": b"match_id=123",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"accept-encoding", b"gzip, deflate")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    app.dependency_overrides[deps.get_redis_client] = lambda: redis
    app.dependency_overrides[deps.verify_token] = lambda: None
    try:
        streaming = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(redis.read_started.wait(), timeout=2)  # the stream waits on the first read
        assert not [message for message in sent if message["type"] == "http.response.start"]  # no body yet
        disconnect.set()
        await asyncio.wait_for(streaming, timeout=2)  # the disconnect must complete without RuntimeError
    finally:
        app.dependency_overrides.pop(deps.get_redis_client, None)
        app.dependency_overrides.pop(deps.verify_token, None)

    statuses = [message["status"] for message in sent if message["type"] == "http.response.start"]
    assert statuses == [200]
    assert redis.zsets.get(live.lease_key("123")) == {}  # the lease is released after the disconnect
