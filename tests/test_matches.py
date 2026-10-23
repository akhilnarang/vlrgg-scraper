import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from app import constants
from app.enums import MatchStatus
from app.exceptions import ScrapingError
from app.services import matches

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_match_details_follow_the_public_response_contract(http_response):
    response = http_response("https://www.vlr.gg/12345", (FIXTURE_DIR / "match_12345.html").read_bytes())

    with patch("httpx2.AsyncClient.get", return_value=response):
        result = await matches.match_by_id("12345", AsyncMock())

    assert [(team.name, team.score, team.tag) for team in result.teams] == [("Team A", 2, "A"), ("Team B", 1, "B")]
    assert result.event.id == "2283"
    assert result.event.series == "Event Series"
    assert result.event.patch == "13.05"
    assert result.map_count == 2
    assert [(item.number, item.map, [team.score for team in item.teams], item.winner) for item in result.data] == [
        (1, "Lotus", [13, 10], "Team A"),
        (3, "Split", [None, None], None),
    ]
    member = result.data[0].members[0]
    assert (member.id, member.name, member.team) == ("2114", "Kinguyen", "Team A")
    assert (member.agents[0].title, member.rating, member.kills) == ("Raze", 1.42, 29)
    # The halftime "Side swap" spacer column must not shift or break the round timeline.
    assert [
        (round_.round_number, round_.round_score, round_.winner, round_.side) for round_ in result.data[0].rounds
    ] == [
        (1, "0-1", "team2", "attack"),
        (2, "1-1", "team1", "defense"),
        (13, "2-1", "", ""),
    ]
    assert [(stream.name, str(stream.url)) for stream in result.videos.streams] == [
        ("VCT", "https://www.youtube.com/@ValorantEsports/live"),
        ("VAL KR", "https://play.sooplive.co.kr/valorant"),
    ]
    assert [(vod.name, str(vod.url)) for vod in result.videos.vods] == [("Map 1", "https://youtu.be/abc123?t=132")]
    assert [(v.team, v.action, v.map) for v in result.veto] == [(None, "unknown", "Map ban: Bind, Haven")]
    assert [
        (v.team, v.action, v.map)
        for v in matches.parse_veto(["Paper Rex ban Bind", "FNC pick Lotus", "Sunset remains"])
    ] == [
        ("Paper Rex", "ban", "Bind"),
        ("FNC", "pick", "Lotus"),
        (None, "remains", "Sunset"),
    ]

    decider = (FIXTURE_DIR / "match_12345.html").read_text().replace("2 N/A", "2 Haven").encode()
    with patch("httpx2.AsyncClient.get", return_value=http_response("https://www.vlr.gg/12345", decider)):
        decider_result = await matches.match_by_id("12345", AsyncMock())

    assert [(item.number, item.map) for item in decider_result.data] == [(1, "Lotus"), (3, "Split")]

    upcoming = (
        (FIXTURE_DIR / "match_12345.html").read_text().replace("1 Lotus", "1 TBD").replace("3 Split", "3 TBD").encode()
    )
    with patch("httpx2.AsyncClient.get", return_value=http_response("https://www.vlr.gg/12345", upcoming)):
        upcoming_result = await matches.match_by_id("12345", AsyncMock())

    assert upcoming_result.data == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeline", "winners"),
    [
        ([(1, "1-0"), (2, "1-0"), (3, "1-1")], ["team1", "", "team2"]),
        ([(1, "1-0"), (2, "3-0"), (3, "3-1")], ["team1", "", "team2"]),
        ([(1, "1-0"), (3, "2-0"), (4, "2-1")], ["team1", "", "team2"]),
        ([(1, "1-0"), (1, "2-0"), (2, "2-1")], ["team1", "", "team2"]),
    ],
    ids=["duplicate-score", "score-jump", "missing-round", "duplicate-round"],
)
async def test_round_winners_require_a_consecutive_single_round_score_change(timeline, winners, http_response):
    soup = BeautifulSoup((FIXTURE_DIR / "match_12345.html").read_bytes(), "lxml")
    row = soup.select_one('.vm-stats-game[data-game-id="283202"] .vlr-rounds-row')
    for column in row.select(".vlr-rounds-row-col:not(.mod-teams)"):
        column.decompose()
    for number, score in timeline:
        column = soup.new_tag("div", attrs={"class": "vlr-rounds-row-col", "title": score})
        round_number = soup.new_tag("div", attrs={"class": "rnd-num"})
        round_number.string = str(number)
        column.append(round_number)
        row.append(column)
    response = http_response("https://www.vlr.gg/12345", str(soup).encode())

    with patch("httpx2.AsyncClient.get", return_value=response):
        result = await matches.match_by_id("12345", AsyncMock())

    assert [round_.round_number for round_ in result.data[0].rounds] == [number for number, _ in timeline]
    assert [round_.winner for round_ in result.data[0].rounds] == winners


def test_display_strings_follow_accept_language(monkeypatch, http_response):
    """Localized labels come from the device locale header; no header keeps the legacy English payload."""
    from fastapi import FastAPI
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.testclient import TestClient

    from app import i18n
    from app.api import deps
    from app.api.v1.endpoints.matches import router

    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=0)
    app.middleware("http")(i18n.localize_response)
    app.include_router(router, prefix="/api/v1/matches")
    store = {}
    redis = AsyncMock()
    redis.get.side_effect = store.get
    redis.set.side_effect = lambda key, value, ttl: store.__setitem__(key, value)
    app.dependency_overrides[deps.get_redis_client] = lambda: redis
    monkeypatch.setattr("app.cache.cache.settings.ENABLE_CACHE", True)

    response = http_response("https://www.vlr.gg/12345", (FIXTURE_DIR / "match_12345.html").read_bytes())
    with (
        patch("httpx2.AsyncClient.get", return_value=response) as vlr_get,
        patch("app.services.matches.get_team_data", AsyncMock(return_value=[])),
    ):
        client = TestClient(app)
        english = client.get("/api/v1/matches/12345")
        hindi = client.get("/api/v1/matches/12345", headers={"Accept-Language": "hi-IN,hi;q=0.9,en;q=0.8"})
        unknown = client.get("/api/v1/matches/12345", headers={"Accept-Language": "xx"})

    assert english.status_code == hindi.status_code == 200
    assert vlr_get.call_count == 1
    assert english.headers["content-language"] == "en" and hindi.headers["content-language"] == "hi"
    en_event, hi_event = english.json()["event"], hindi.json()["event"]
    assert (en_event["status"], en_event["status_label"]) == ("completed", "Completed")
    assert (hi_event["status"], hi_event["status_label"]) == ("completed", "समाप्त")
    assert (english.json()["veto"][0]["action_label"], hindi.json()["veto"][0]["action_label"]) == ("Unknown", "अज्ञात")
    assert unknown.json() == english.json() and unknown.headers["content-language"] == "en"

    assert i18n.resolve("pt") == "pt-BR" and i18n.resolve("de;q=0.5, ko;q=0.8") == "ko"
    assert i18n.resolve("de;q=0, ko") == "ko"
    assert i18n.resolve("de ;Q=0.1, ko;q=0.5") == "ko" and i18n.resolve("de;q=2, ko;q=1") == "ko"
    assert unknown.headers["vary"] == "Accept-Encoding, Accept-Language"


def test_unreachable_vlr_returns_503(monkeypatch):
    """A DNS failure or blocked IP is an upstream outage, so clients must see 503, not an unhandled 500."""
    import httpx2
    from fastapi.testclient import TestClient

    from app.core.config import settings

    monkeypatch.setattr(settings, "API_KEYS", {"test": "test-key"})
    from app.main import app

    with patch(
        "httpx2.AsyncClient.get", AsyncMock(side_effect=httpx2.ConnectError("[Errno -2] Name or service not known"))
    ):
        response = TestClient(app).get("/api/v1/matches/12345", headers={"Authorization": "Bearer test-key"})

    assert (response.status_code, response.json()) == (503, {"detail": "VLR.gg is unreachable"})


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
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAPPING", False)

    with patch("httpx2.AsyncClient.get", side_effect=lambda url, *_args, **_kwargs: responses[url]):
        result = await matches.match_list(AsyncMock())

    names = {match.id: (match.team1.name, match.team2.name) for match in result}
    assert names["684611"] == ("FULL SENSE", "FUT Esports")
    assert names["684612"] == ("LEVIATÁN", "Global Esports")
    assert names["684610"] == ("Team Vitality", "Dragon Ranger Gaming")


def test_match_list_shows_the_broadcast_map_on_its_live_match(monkeypatch):
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app import schemas
    from app.api import deps
    from app.api.v1.endpoints.matches import router
    from app.schemas.matches import Event, MatchData, MatchVideos, MatchWithDetails, Team, TeamWithImage, VideoScore

    detail = MatchWithDetails(
        teams=[
            TeamWithImage(id="1", name="Alpha", tag="ALP", score=0, img="https://cdn.vlr.gg/a.png"),
            TeamWithImage(id="2", name="Beta", tag="BET", score=1, img="https://cdn.vlr.gg/b.png"),
        ],
        bans=[],
        event=Event(id="99", img="https://cdn.vlr.gg/e.png", series="Series", stage="Playoffs", status="live"),
        videos=MatchVideos(streams=[], vods=[]),
        map_count=2,
        total_maps=3,
        data=[
            MatchData(
                number=1,
                map="Ascent",
                teams=[Team(name="Beta", score=13), Team(name="Alpha", score=9)],
                members=[],
                rounds=[],
                winner="Beta",
            ),
            MatchData(
                number=2,
                map="Bind",
                teams=[Team(name="Alpha", score=3), Team(name="Beta", score=4)],
                members=[],
                rounds=[],
                live=True,
            ),
        ],
        previous_encounters=[],
    )
    listed = [
        # VLR lists Beta first here, so the map score must follow the listing's team order, not the details'.
        schemas.Match(
            id="123",
            team1=schemas.MatchTeam(name="Beta", id="2", score=1),
            team2=schemas.MatchTeam(name="Alpha", id="1", score=0),
            status="live",
            time="2026-10-16T10:00:00Z",
            event="Event",
            series="Series",
            event_id="99",
        ),
        schemas.Match(
            id="456",
            team1=schemas.MatchTeam(name="Alpha", id="1"),
            team2=schemas.MatchTeam(name="Gamma", id="3"),
            status="upcoming",
            time="2026-10-17T10:00:00Z",
            event="Event",
            series="Series",
            event_id="99",
        ),
    ]

    def video(status, observed_at):
        return VideoScore.model_validate(
            {
                "status": status,
                "observed_at": observed_at,
                "map_number": 2,
                "teams": [{"code": "ALP", "name": "Alpha", "score": 5}, {"code": "BET", "name": "Beta", "score": 7}],
            }
        ).model_dump_json()

    stored = {
        "matches": schemas.MatchListAdapter.dump_json(listed),
        constants.PUSH_DETAILS_KEY: json.dumps({"123": detail.model_dump(mode="json")}),
        constants.VIDEO_SCORE_KEY: video("ok", int(time.time())),
    }
    # store_score writes a map's round history once a read of that map is verified for the match.
    verified = {constants.VIDEO_ROUNDS_KEY.format("123", 2)}
    redis_down = False

    def exists(key):
        if redis_down:
            raise RedisConnectionError("Redis is down")
        return int(key in verified)

    redis = AsyncMock()
    redis.get.side_effect = stored.get
    redis.exists.side_effect = exists
    monkeypatch.setattr("app.cache.cache.settings.ENABLE_CACHE", True)
    monkeypatch.setattr(matches.settings, "ENABLE_LIVE_PUSH", True)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/matches")
    app.dependency_overrides[deps.get_redis_client] = lambda: redis

    with TestClient(app) as client:
        fresh = client.get("/api/v1/matches/").json()
        # During a pause or a break the tracker re-sends the last score it read with an error status.
        stored[constants.VIDEO_SCORE_KEY] = video("error", int(time.time()) - constants.VIDEO_STALE_SECONDS - 1)
        paused = client.get("/api/v1/matches/").json()
        # Another live match keeps the lookup running, so only the ended match's own status hides its map.
        stored["matches"] = schemas.MatchListAdapter.dump_json(
            [
                listed[0].model_copy(update={"status": MatchStatus.COMPLETED}),
                listed[1].model_copy(update={"status": MatchStatus.LIVE}),
            ]
        )
        ended = client.get("/api/v1/matches/").json()
        # The same teams' earlier series left its last score, but no read was verified for this match.
        stored["matches"] = schemas.MatchListAdapter.dump_json(listed)
        rounds_key = verified.pop()
        rematch = client.get("/api/v1/matches/").json()
        verified.add(rounds_key)
        redis_down = True
        during_outage = client.get("/api/v1/matches/")

    bind = {"name": "Bind", "number": 2, "scores": [7, 5]}
    assert [match["current_map"] for match in fresh] == [bind, None]
    assert [match["team1"]["score"] for match in fresh] == [1, None]
    # The last known score stays while the match is live, however long the tracker has not read.
    assert [match["current_map"] for match in paused] == [bind, None]
    assert [match["current_map"] for match in ended] == [None, None]
    assert [match["current_map"] for match in rematch] == [None, None]
    # The map score is an extra: a Redis outage drops it instead of failing the list.
    assert during_outage.status_code == 200
    assert [match["current_map"] for match in during_outage.json()] == [None, None]


@pytest.mark.asyncio
async def test_completed_matches_clamp_pages_and_keep_results_in_order(monkeypatch, http_get):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAPPING", False)
    pages = {
        constants.PAST_MATCHES_URL: (FIXTURE_DIR / "matches_results_page1.html").read_bytes(),
        matches.completed_matches_url(2): (FIXTURE_DIR / "matches_results_page2.html").read_bytes(),
    }

    with patch("httpx2.AsyncClient.get", side_effect=http_get(pages)) as get:
        result = await matches.get_completed_matches(AsyncMock(), pages=9999)

    assert len(result) == 100
    assert len({match.id for match in result}) == 100
    assert result[0].id == "670476"
    assert result[50].id == "684615"
    assert get.call_count <= constants.MAX_PAGINATION_PAGES


@pytest.mark.asyncio
async def test_completed_matches_do_not_return_partial_results(monkeypatch, http_get):
    monkeypatch.setattr(matches.settings, "ENABLE_ID_MAPPING", False)
    pages = {constants.PAST_MATCHES_URL: (FIXTURE_DIR / "matches_results_page1.html").read_bytes()}
    failures = {matches.completed_matches_url(2): 502}

    with patch("httpx2.AsyncClient.get", side_effect=http_get(pages, failures=failures)), pytest.raises(ScrapingError):
        await matches.get_completed_matches(AsyncMock(), pages=2)


def test_live_update_api_stores_token_and_favorites(monkeypatch, make_database):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.api import deps
    from app.api.v1.endpoints.live_updates import router
    from app.core import connections
    from app.db.migrations import upgrade_to_head

    database_url = make_database()
    asyncio.run(upgrade_to_head(database_url))
    # TestClient and asyncio.run each use a fresh event loop, and asyncpg connections cannot outlive theirs.
    engine = create_async_engine(database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(deps.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(deps.settings, "API_KEYS", {"test": "secret"})

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/live-updates")
    redis = AsyncMock()
    redis.get.return_value = None
    app.dependency_overrides[deps.get_redis_client] = lambda: redis
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    base = "/api/v1/live-updates/clients/11111111-1111-4111-8111-111111111111"

    try:
        assert client.get(f"{base}/favorites", headers=headers).status_code == 404
        token_response = client.put(f"{base}/token", headers=headers, json={"token": "AABB"})
        assert token_response.status_code == 204
        assert token_response.headers["Cache-Control"] == "no-store"
        fcm_token = {"token": "dGVzdA_x-1:APA91bH", "platform": "android"}
        assert client.put(f"{base}/token", headers=headers, json=fcm_token).status_code == 204

        from app.db.models import Client

        async def live_updates_setting():
            async with sessions() as session:
                return (await session.get(Client, "11111111-1111-4111-8111-111111111111")).live_updates

        assert asyncio.run(live_updates_setting()) is None
        assert (
            client.put(f"{base}/token", headers=headers, json={**fcm_token, "live_updates": False}).status_code == 204
        )
        assert client.put(f"{base}/token", headers=headers, json=fcm_token).status_code == 204
        assert asyncio.run(live_updates_setting()) is False
        with patch("app.services.push.deliver_instant_start", AsyncMock()) as instant_start:
            turned_off = client.post(f"{base}/matches/123/live-activity", headers=headers)
        assert turned_off.status_code == 409
        instant_start.assert_not_awaited()
        assert client.put(f"{base}/token", headers=headers, json={"token": fcm_token["token"]}).status_code == 422
        assert (
            client.put(f"{base}/token", headers=headers, json={"token": "AABB", "live_updates": True}).status_code
            == 204
        )
        assert (
            client.put(
                f"{base}/favorites",
                headers=headers,
                json={"teams": ["1"], "matches": ["2"], "players": ["3"], "events": ["4"]},
            ).status_code
            == 204
        )
        favorites_response = client.get(f"{base}/favorites", headers=headers)
        assert favorites_response.headers["Cache-Control"] == "no-store"
        assert favorites_response.json() == {
            "teams": ["1"],
            "matches": ["2"],
            "players": ["3"],
            "events": ["4"],
        }
        assert client.put(f"{base}/favorites", headers=headers, json={"teams": ["5", "1"]}).status_code == 204
        # A zero-padded ID would overflow VARCHAR(10) in PostgreSQL and 500 instead of 422.
        assert client.put(f"{base}/favorites", headers=headers, json={"teams": ["00000000001"]}).status_code == 422
        assert client.get(f"{base}/favorites", headers=headers).json()["teams"] == ["1", "5"]
        removal = {"teams": ["1"], "matches": ["2"], "events": ["9"]}
        assert client.request("DELETE", f"{base}/favorites", headers=headers, json=removal).status_code == 204
        assert client.get(f"{base}/favorites", headers=headers).json() == {
            "teams": ["5"],
            "matches": [],
            "players": ["3"],
            "events": ["4"],
        }
        unknown = "/api/v1/live-updates/clients/33333333-3333-4333-8333-333333333333/favorites"
        assert client.request("DELETE", unknown, headers=headers, json=removal).status_code == 404

        mock_apns = AsyncMock()
        mock_apns.create_channel.return_value = "channel-live-123"
        monkeypatch.setattr(connections, "apns_client", mock_apns)

        from app.schemas.matches import Event, MatchData, MatchVideos, MatchWithDetails, Round, Team, TeamWithImage

        live_detail = MatchWithDetails(
            teams=[
                TeamWithImage(id="1", name="Alpha", tag="ALP", score=1, img="https://cdn.vlr.gg/a.png"),
                TeamWithImage(id="2", name="Beta", tag="BET", score=0, img="https://cdn.vlr.gg/b.png"),
            ],
            bans=[],
            event=Event(
                id="99",
                img="https://cdn.vlr.gg/e.png",
                series="Series",
                stage="Playoffs: Grand Final",
                status="live",
            ),
            videos=MatchVideos(streams=[], vods=[]),
            map_count=1,
            total_maps=3,
            data=[
                MatchData(
                    number=1,
                    map="Ascent",
                    teams=[Team(name="Beta", score=14), Team(name="Alpha", score=12)],
                    members=[],
                    rounds=[
                        Round(round_number=1, round_score="1-0", winner="team1", side="attack", win_type="Elimination"),
                        Round(
                            round_number=2, round_score="1-1", winner="team2", side="defense", win_type="Elimination"
                        ),
                        Round(round_number=3, round_score="2-1", winner="team1", side="attack", win_type="Elimination"),
                    ],
                    winner="Beta",
                ),
                MatchData(
                    number=2,
                    map="Bind",
                    teams=[Team(name="Alpha", score=12), Team(name="Beta", score=11)],
                    members=[],
                    rounds=[
                        Round(round_number=1, round_score="0-1", winner="team2", side="defense", win_type="Elimination")
                    ],
                    live=True,
                ),
            ],
            previous_encounters=[],
        )
        completed_detail = live_detail.model_copy(
            update={"event": live_detail.event.model_copy(update={"status": "completed"})}
        )
        assert live_detail.model_dump()["current_map"] == {"name": "Bind", "scores": [12, 11], "number": 2}
        assert completed_detail.model_dump()["current_map"] is None

        with patch("app.services.matches.match_by_id", AsyncMock(return_value=live_detail)):
            start_response = client.post(f"{base}/matches/123/live-activity", headers=headers)
            assert start_response.status_code == 204
            assert start_response.headers["Cache-Control"] == "no-store"
            mock_apns.create_channel.assert_awaited_once()
            mock_apns.send_start.assert_awaited_once()
            token_arg, channel_arg, state_arg = mock_apns.send_start.call_args[0]
            assert (token_arg, channel_arg) == ("aabb", "channel-live-123")
            assert (state_arg.total_maps, state_arg.current_map.number) == (3, 2)

            retrigger = client.post(f"{base}/matches/123/live-activity", headers=headers)
            assert retrigger.status_code == 204
            assert mock_apns.create_channel.await_count == 1
            assert mock_apns.send_start.await_count == 2

        with patch("app.services.matches.match_by_id", AsyncMock(return_value=completed_detail)):
            assert client.post(f"{base}/matches/456/live-activity", headers=headers).status_code == 400

        from app.services.apns import APNsError
        from app.services.subscription_store import SubscriptionStore

        mock_apns.send_start.side_effect = APNsError(410, "Unregistered")
        with patch("app.services.matches.match_by_id", AsyncMock(return_value=live_detail)):
            failed_ios = client.post(f"{base}/matches/789/live-activity", headers=headers)
        assert (failed_ios.status_code, failed_ios.json()) == (503, {"detail": "APNs start failed: Unregistered"})
        assert client.post(f"{base}/matches/123/live-activity", headers=headers).status_code == 404

        async def saved_channel():
            async with sessions() as session:
                return await SubscriptionStore(session).get_match("789")

        assert asyncio.run(saved_channel()).channel_id == "channel-live-123"

        unknown = "/api/v1/live-updates/clients/99999999-9999-4999-8999-999999999999"
        assert client.post(f"{unknown}/matches/123/live-activity", headers=headers).status_code == 404

        from datetime import timedelta

        from firebase_admin import messaging

        from app.services.push import Routing

        android_client = "/api/v1/live-updates/clients/22222222-2222-4222-8222-222222222222"
        assert (
            client.put(
                f"{android_client}/token", headers=headers, json={"token": "tok:123", "platform": "android"}
            ).status_code
            == 204
        )
        fcm_app = MagicMock()
        send_fcm = AsyncMock(return_value=messaging.BatchResponse([messaging.SendResponse({"name": "sent"}, None)]))
        with (
            patch("app.services.matches.match_by_id", AsyncMock(return_value=live_detail)),
            patch("app.core.config.settings.GOOGLE_APPLICATION_CREDENTIALS", ""),
        ):
            unavailable = client.post(f"{android_client}/matches/123/live-activity", headers=headers)
            assert (unavailable.status_code, unavailable.json()) == (503, {"detail": "FCM client is not configured"})

        with (
            patch("app.services.matches.match_by_id", AsyncMock(return_value=live_detail)),
            patch("app.core.config.settings.GOOGLE_APPLICATION_CREDENTIALS", "/fake/creds.json"),
            patch("app.services.fcm.get_app", return_value=fcm_app),
            patch("firebase_admin.messaging.send_each_async", send_fcm),
        ):
            android_resp = client.post(f"{android_client}/matches/123/live-activity", headers=headers)
            assert android_resp.status_code == 204
            send_fcm.assert_awaited_once()
            message = send_fcm.call_args.kwargs["messages"][0]
            assert send_fcm.call_args.kwargs == {"messages": [message], "dry_run": False, "app": fcm_app}
            assert message.token == "tok:123"
            assert message.notification is None and message.condition is None
            assert message.android.collapse_key == "match-123"
            assert message.android.priority == "high" and message.android.ttl == timedelta(seconds=120)
            assert message.data["type"] == "match-live-v1"
            state_data = json.loads(message.data["state"])
            assert (state_data["match_id"], state_data["total_maps"], state_data["current_map"]["number"]) == (
                "123",
                3,
                2,
            )
            assert state_data["map_winners"] == ["2", None, None]
            assert state_data["map_round_winners"] == [
                {"map_number": 1, "winners": [1, 0, 1], "scores": [12, 14]},
                {"map_number": 2, "winners": [1], "scores": [12, 11]},
                {"map_number": 3, "winners": [], "scores": []},
            ]
            # Map 1's confirmed win floors Beta's series score, which the header lags at 0.
            assert [(team["id"], team["score"]) for team in state_data["teams"]] == [("1", 1), ("2", 1)]
            assert state_data["stage"] == "Playoffs: Grand Final"

            assert (
                client.put(f"{android_client}/favorites", headers=headers, json={"matches": ["123"]}).status_code == 204
            )
            assert client.put(f"{android_client}/token", headers=headers, json={"token": "CCDD"}).status_code == 204

            async def pending_ios_starts():
                async with sessions() as session:
                    return await SubscriptionStore(session).pending_starts(Routing("123", None, [], []))

            assert asyncio.run(pending_ios_starts()) == [("22222222-2222-4222-8222-222222222222", "ccdd")]
            assert (
                client.put(
                    f"{android_client}/token", headers=headers, json={"token": "tok:123", "platform": "android"}
                ).status_code
                == 204
            )

            send_fcm.return_value = messaging.BatchResponse(
                [messaging.SendResponse(None, messaging.UnregisteredError("registration token expired"))]
            )
            failed = client.post(f"{android_client}/matches/123/live-activity", headers=headers)
            assert (failed.status_code, failed.json()) == (503, {"detail": "FCM start failed"})
            assert client.post(f"{android_client}/matches/123/live-activity", headers=headers).status_code == 404

        assert client.delete(f"{base}/token", headers=headers).status_code == 204
    finally:
        asyncio.run(engine.dispose())


RIOT_WEB_VODS_FIXTURE = FIXTURE_DIR / "riot_web" / "eventdetails_champions2026_lowerfinal.json"


def _vlr_details(numbers, scores):
    """Build the VLR details of the Champions lower final with the given played maps and series score."""
    from app import schemas

    return schemas.MatchWithDetails.model_validate(
        {
            "teams": [
                {
                    "name": "100 Thieves",
                    "img": "https://cdn.test/100t.png",
                    "score": scores[0],
                    "id": "105748037960121143",
                },
                {
                    "name": "Team Vitality",
                    "img": "https://cdn.test/vit.png",
                    "score": scores[1],
                    "id": "106013381894847385",
                },
            ],
            "bans": [],
            "event": {
                "id": "2283",
                "img": "https://cdn.test/champions.png",
                "series": "Champions 2026",
                "stage": "Lower Final",
            },
            "videos": {"streams": [], "vods": [{"name": "Map 1", "url": "https://youtu.be/MZjaOtjtRlU?t=1"}]},
            "map_count": len(numbers),
            "total_maps": 5,
            "data": [
                {
                    "number": number,
                    "map": f"Map {number}",
                    "teams": [
                        {"name": "100 Thieves", "score": 13},
                        {"name": "Team Vitality", "score": 9},
                    ],
                    "members": [],
                    "rounds": [],
                    "winner": "100 Thieves",
                }
                for number in numbers
            ],
            "previous_encounters": [],
        }
    )


@pytest.mark.asyncio
async def test_match_vods_start_each_vlr_map_at_its_riot_offset(monkeypatch, make_database):
    """Stored Riot offsets ride along with VLR's own VODs, ordered and only when the maps agree."""
    import httpx2
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.v1.endpoints.matches import get_match_by_id
    from app.core import connections
    from app.core.config import settings
    from app.cron import riot_web as riot_web_cron
    from app.db.migrations import upgrade_to_head
    from app.db.models import RiotWebMatch
    from app.enums import VodProvider
    from app.schemas.riot_web import StoredDetails
    from app.services import match_vods

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(settings, "RIOT_WEB_API_KEY", "test-key")

    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            RiotWebMatch(
                riot_id="115576361461208325",
                start_time=now,
                league_name="Champions",
                teams=[{"name": "100 Thieves"}, {"name": "Team Vitality"}],
                vlr_match_id="12345",
            )
        )

    payload = json.loads(RIOT_WEB_VODS_FIXTURE.read_text())
    games = payload["data"]["event"]["match"]["games"]
    # English moved last, and an id that is not a YouTube id, must both sort/filter out.
    games[0]["vods"].append(games[0]["vods"].pop(0))
    games[0]["vods"].insert(0, {"parameter": "x&y=1", "locale": "en-US", "provider": "youtube", "startMillis": 1000})
    # A Twitch VOD listed first still sorts behind YouTube within English.
    games[1]["vods"].insert(
        0, {"parameter": "2896791742", "locale": "en-US", "provider": "twitch", "startMillis": 2685000}
    )
    # A completed map can legitimately have no VOD at all.
    games[2]["vods"] = []

    requested = []

    async def router(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        requested.append(request)
        return httpx2.Response(200, json=payload, request=request)

    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        await riot_web_cron.riot_web_details_cron({"redis": AsyncMock()})

    assert [
        (
            request.url.path,
            request.url.params.get("id"),
            request.url.params.get("hl"),
            request.url.params.get("sport"),
            request.headers["x-api-key"],
        )
        for request in requested
    ] == [("/persisted/val/getEventDetails", "115576361461208325", "en-US", "val", "test-key")]

    async with sessions() as session:
        row = await session.scalar(select(RiotWebMatch).where(RiotWebMatch.riot_id == "115576361461208325"))
        stored = StoredDetails.model_validate(row.details)
        tournament_id, fetched_at = row.tournament_id, row.details_fetched_at
    assert (tournament_id, fetched_at is not None) == ("115576361459045501", True)
    assert stored.game_wins == {"100 Thieves": 1, "Team Vitality": 3}
    assert [(game.number, game.state, len(game.vods)) for game in stored.games] == [
        (1, "completed", 10),
        (2, "completed", 10),
        (3, "completed", 0),
        (4, "completed", 9),
        (5, "unneeded", 0),
    ]

    details = _vlr_details([1, 2, 3, 4], [1, 3])
    with patch("app.services.matches.cached_match_by_id", AsyncMock(return_value=details)):
        response = await get_match_by_id("12345", AsyncMock())

    assert [(item.map_number, [link.locale for link in item.links]) for item in response.map_vods] == [
        (1, ["en-US", "es-MX", "fr-FR", "id-ID", "ja-JP", "pt-BR", "th-TH", "tr-TR", "vi-VN"]),
        (2, ["en-US", "en-US", "es-MX", "fr-FR", "id-ID", "ja-JP", "pt-BR", "th-TH", "tr-TR", "vi-VN"]),
        (4, ["en-US", "es-MX", "fr-FR", "id-ID", "ja-JP", "pt-BR", "th-TH", "tr-TR", "vi-VN"]),
    ]
    assert {link.provider for link in response.map_vods[0].links} == {VodProvider.YOUTUBE}
    assert response.model_dump(mode="json")["map_vods"][0]["links"][0] == {
        "provider": "youtube",
        "locale": "en-US",
        "start_seconds": 1,
        "url": "https://www.youtube.com/watch?v=MZjaOtjtRlU&t=1s",
    }
    assert [str(link.url) for link in response.map_vods[1].links[:2]] == [
        "https://www.youtube.com/watch?v=MZjaOtjtRlU&t=2685s",
        "https://www.twitch.tv/videos/2896791742?t=0h44m45s",
    ]
    # Riot is an overlay: VLR's own video list is untouched.
    assert [str(vod.url) for vod in response.videos.vods] == ["https://youtu.be/MZjaOtjtRlU?t=1"]

    # The numbers are Riot's only map key, so any disagreement with VLR drops every offset.
    assert await match_vods.map_vods("12345", _vlr_details([1, 2, 3], [1, 3])) == []
    assert await match_vods.map_vods("12345", _vlr_details([1, 2, 3, 4], [2, 2])) == []
    # A reversed series score is a different result even though the multiset of wins matches.
    assert await match_vods.map_vods("12345", _vlr_details([1, 2, 3, 4], [3, 1])) == []


@pytest.mark.asyncio
async def test_map_vods_are_absent_without_stored_riot_data(monkeypatch, make_database):
    """With no fetched series, or the key unset, the match response is what VLR alone returns."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.v1.endpoints.matches import get_match_by_id
    from app.core import connections
    from app.core.config import settings
    from app.db.migrations import upgrade_to_head
    from app.services import match_vods

    database_url = make_database()
    await upgrade_to_head(database_url)
    engine = create_async_engine(database_url)
    monkeypatch.setattr(connections, "subscription_sessions", async_sessionmaker(engine, expire_on_commit=False))

    details = _vlr_details([1, 2, 3, 4], [1, 3])
    monkeypatch.setattr(settings, "RIOT_WEB_API_KEY", "test-key")
    with patch("app.services.matches.cached_match_by_id", AsyncMock(return_value=details)):
        response = await get_match_by_id("12345", AsyncMock())
    assert response.map_vods == []

    # With the key unset, a cache hit must not cost a database round trip.
    monkeypatch.setattr(settings, "RIOT_WEB_API_KEY", None)
    opened = []

    def open_session():
        opened.append(True)
        raise RuntimeError("no database session may be opened while RIOT_WEB_API_KEY is unset")

    monkeypatch.setattr(connections, "subscription_sessions", open_session)
    assert await match_vods.map_vods("12345", details) == []
    assert opened == []

    with patch("app.services.matches.cached_match_by_id", AsyncMock(return_value=details)):
        disabled = await get_match_by_id("12345", AsyncMock())

    assert disabled.model_dump(mode="json") == response.model_dump(mode="json")
    assert [(team.name, team.score) for team in disabled.teams] == [("100 Thieves", 1), ("Team Vitality", 3)]
    assert [str(vod.url) for vod in disabled.videos.vods] == ["https://youtu.be/MZjaOtjtRlU?t=1"]
    assert [item.number for item in disabled.data] == [1, 2, 3, 4]
