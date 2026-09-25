import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import constants
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
    assert result.map_count == 1
    assert [(item.map, [team.score for team in item.teams]) for item in result.data] == [("Lotus", [13, 10])]
    member = result.data[0].members[0]
    assert (member.id, member.name, member.team) == ("2114", "Kinguyen", "Team A")
    assert (member.agents[0].title, member.rating, member.kills) == ("Raze", 1.42, 29)
    # VLR's stream grid: broadcasts only (watch parties excluded), including off-platform links.
    assert [(stream.name, str(stream.url)) for stream in result.videos.streams] == [
        ("VCT", "https://www.youtube.com/@ValorantEsports/live"),
        ("VAL KR", "https://play.sooplive.co.kr/valorant"),
    ]
    assert [(vod.name, str(vod.url)) for vod in result.videos.vods] == [("Map 1", "https://youtu.be/abc123?t=132")]
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
    # Repeat requests within the TTL are served from cache, still localized per request.
    assert vlr_get.call_count == 1
    assert english.headers["content-language"] == "en" and hindi.headers["content-language"] == "hi"
    en_event, hi_event = english.json()["event"], hindi.json()["event"]
    assert (en_event["status"], en_event["status_label"]) == ("completed", "Completed")
    assert (hi_event["status"], hi_event["status_label"]) == ("completed", "समाप्त")  # existing field never changes
    assert (english.json()["veto"][0]["action_label"], hindi.json()["veto"][0]["action_label"]) == ("Unknown", "अज्ञात")
    assert unknown.json() == english.json() and unknown.headers["content-language"] == "en"

    assert i18n.resolve("pt") == "pt-BR" and i18n.resolve("de;q=0.5, ko;q=0.8") == "ko"
    assert i18n.resolve("de;q=0, ko") == "ko"  # q=0 means not acceptable
    assert i18n.resolve("de ;Q=0.1, ko;q=0.5") == "ko" and i18n.resolve("de;q=2, ko;q=1") == "ko"  # lenient syntax
    assert unknown.headers["vary"] == "Accept-Encoding, Accept-Language"  # GZip's Vary must survive


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


def test_live_update_api_stores_token_and_favorites(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api import deps
    from app.api.v1.endpoints.live_updates import router
    from app.core import connections
    from app.db.engine import create_engine
    from app.db.migrations import upgrade_to_head

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    asyncio.run(upgrade_to_head(database_url))
    engine = create_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    monkeypatch.setattr(deps.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(deps.settings, "API_KEYS", {"test": "secret"})

    # The CDN logo manifest, as live push startup loads it; team 1 has a mirrored logo, team 2 does not.
    import httpx2

    from app.services import team_logos

    cdn_logo = "https://files.akhilnarang.dev/cdn/valorant/teams/1.png"
    manifest = httpx2.MockTransport(lambda request: httpx2.Response(200, json={"1": {"logo": {"url": cdn_logo}}}))
    real_client = httpx2.AsyncClient
    monkeypatch.setattr(team_logos.httpx2, "AsyncClient", lambda **kwargs: real_client(transport=manifest, **kwargs))
    monkeypatch.setattr(team_logos, "_logos", {})
    asyncio.run(team_logos.load())

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/live-updates")
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
        # Omitting platform keeps existing iOS clients working, and iOS still requires a hex APNs token.
        assert client.put(f"{base}/token", headers=headers, json={"token": fcm_token["token"]}).status_code == 422
        assert client.put(f"{base}/token", headers=headers, json={"token": "AABB"}).status_code == 204
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

        # Instant Live Activity start for an in-progress match
        mock_apns = AsyncMock()
        mock_apns.create_channel.return_value = "channel-live-123"
        monkeypatch.setattr(connections, "apns_client", mock_apns)

        from app.schemas.matches import Event, MatchData, MatchVideos, MatchWithDetails, Team, TeamWithImage

        live_detail = MatchWithDetails(
            teams=[
                TeamWithImage(id="1", name="Alpha", tag="ALP", score=1, img="https://cdn.vlr.gg/a.png"),
                TeamWithImage(id="2", name="Beta", tag="BET", score=0, img="https://cdn.vlr.gg/b.png"),
            ],
            bans=[],
            event=Event(id="99", img="https://cdn.vlr.gg/e.png", series="Series", stage="Stage", status="live"),
            videos=MatchVideos(streams=[], vods=[]),
            map_count=1,
            total_maps=3,
            data=[
                # Beta took map 1 in overtime; VLR can list a map's teams in either order.
                MatchData(
                    map="Ascent",
                    teams=[Team(name="Beta", score=14), Team(name="Alpha", score=12)],
                    members=[],
                    rounds=[],
                ),
                MatchData(
                    map="Bind", teams=[Team(name="Alpha", score=12), Team(name="Beta", score=11)], members=[], rounds=[]
                ),
            ],
            previous_encounters=[],
        )
        completed_detail = live_detail.model_copy(
            update={"event": live_detail.event.model_copy(update={"status": "completed"})}
        )

        with patch("app.services.matches.match_by_id", AsyncMock(return_value=live_detail)):
            start_response = client.post(f"{base}/matches/123/live-activity", headers=headers)
            assert start_response.status_code == 204
            assert start_response.headers["Cache-Control"] == "no-store"
            mock_apns.create_channel.assert_awaited_once()
            mock_apns.send_start.assert_awaited_once()
            token_arg, channel_arg, state_arg = mock_apns.send_start.call_args[0]
            assert (token_arg, channel_arg) == ("aabb", "channel-live-123")
            assert (state_arg.total_maps, state_arg.current_map.number) == (3, 2)

            # Re-triggering reuses the existing broadcast channel without recreating it
            retrigger = client.post(f"{base}/matches/123/live-activity", headers=headers)
            assert retrigger.status_code == 204
            assert mock_apns.create_channel.await_count == 1
            assert mock_apns.send_start.await_count == 2

        with patch("app.services.matches.match_by_id", AsyncMock(return_value=completed_detail)):
            assert client.post(f"{base}/matches/456/live-activity", headers=headers).status_code == 400

        # A rejected first start retains its new channel while removing the token despite the HTTP 503.
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
            # Winner's team ID per finished map; null for the map in progress and the unplayed one.
            assert state_data["map_winners"] == ["2", None, None]
            # Team IDs let clients match map_winners to a team.
            assert [(team["id"], team["score"]) for team in state_data["teams"]] == [("1", 1), ("2", 0)]
            # The CDN logo rides alongside VLR's img, which released apps still require.
            assert [(team["img"], team["logo"]) for team in state_data["teams"]] == [
                ("https://cdn.vlr.gg/a.png", cdn_logo),
                ("https://cdn.vlr.gg/b.png", None),
            ]

            # Android delivery must not suppress a later automatic iOS start for this client.
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

            # Firebase's unregistered response must remove the token despite the HTTP 503.
            send_fcm.return_value = messaging.BatchResponse(
                [messaging.SendResponse(None, messaging.UnregisteredError("registration token expired"))]
            )
            failed = client.post(f"{android_client}/matches/123/live-activity", headers=headers)
            assert (failed.status_code, failed.json()) == (503, {"detail": "FCM start failed"})
            assert client.post(f"{android_client}/matches/123/live-activity", headers=headers).status_code == 404

        assert client.delete(f"{base}/token", headers=headers).status_code == 204
    finally:
        asyncio.run(engine.dispose())
