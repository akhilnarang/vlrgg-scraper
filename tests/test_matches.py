import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import constants
from app.exceptions import ScrapingError
from app.services import matches

FIXTURE_DIR = Path(__file__).parent / "fixtures"


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

    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=0)
    app.middleware("http")(i18n.localize_response)
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
    assert unknown.headers["vary"] == "Accept-Encoding, Accept-Language"  # GZip's Vary must survive


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
    monkeypatch.setattr(connections, "subscription_sessions", async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(deps.settings, "ENABLE_LIVE_PUSH", True)
    monkeypatch.setattr(deps.settings, "API_KEYS", {"test": "secret"})

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
        assert client.delete(f"{base}/token", headers=headers).status_code == 204
    finally:
        asyncio.run(engine.dispose())
