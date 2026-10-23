import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.constants import EVENTS_URL
from app.exceptions import ScrapingError
from app.services import events

FIXTURE_DIR = Path(__file__).parent / "fixtures"
RIOT_FIXTURE_DIR = FIXTURE_DIR / "riot_web"
BRACKETS_TOURNAMENT = "112019354266558216"


@pytest.mark.asyncio
async def test_event_list_returns_full_history_in_order(http_get):
    pages = {
        EVENTS_URL: (FIXTURE_DIR / "events_page1.html").read_bytes(),
        events.events_url(2): (FIXTURE_DIR / "events_page2.html").read_bytes(),
    }
    with patch(
        "httpx2.AsyncClient.get",
        side_effect=http_get(pages, fallback=(FIXTURE_DIR / "events_empty.html").read_bytes()),
    ):
        result = await events.get_events(AsyncMock(), pages=0)

    assert len(result) > 50
    assert len({event.id for event in result}) == len(result)
    assert result[0].id == "2765"


@pytest.mark.asyncio
async def test_event_list_does_not_return_partial_results(http_get):
    pages = {EVENTS_URL: (FIXTURE_DIR / "events_page1.html").read_bytes()}
    failures = {events.events_url(2): 503}

    with patch("httpx2.AsyncClient.get", side_effect=http_get(pages, failures=failures)), pytest.raises(ScrapingError):
        await events.get_events(AsyncMock(), pages=2)


@pytest.mark.asyncio
async def test_event_details_follow_the_current_public_contract(http_get):
    pages = {
        "https://www.vlr.gg/event/2863": (FIXTURE_DIR / "event_2863.html").read_bytes(),
        "https://www.vlr.gg/event/2863/vct-2026-emea-stage-1/group-stage": (
            FIXTURE_DIR / "event_2863_group_stage.html"
        ).read_bytes(),
    }

    with patch("httpx2.AsyncClient.get", side_effect=http_get(pages)):
        result = await events.get_event_by_id("2863")

    assert result.id == "2863"
    assert result.title == "VCT 2026: EMEA Stage 1"
    assert result.subtitle == "Part of the Valorant Champions Tour, Riot's official 2026 tournament circuit."
    assert result.dates == "Apr 1 – May 18, 2026"
    assert result.prize == "TBD"
    assert result.location == "Riot Games Arena, Berlin"
    first_prize = result.prizes[0]
    assert first_prize.team is not None
    assert (first_prize.position, first_prize.team.name) == ("1st", "Team Heretics")
    assert (result.teams[0].name, result.teams[0].seed) == ("FUT Esports", "Alpha #1")
    # The overview shows the playoffs bracket; standings come from the group stage page.
    alpha_leader = result.standings[0]
    assert (alpha_leader.team, alpha_leader.group, alpha_leader.wins, alpha_leader.losses) == (
        "FUT Esports",
        "Group Alpha",
        4,
        1,
    )
    assert (alpha_leader.map_difference, alpha_leader.round_difference, alpha_leader.round_delta) == (3, 13, 13)
    assert {standing.group for standing in result.standings} == {"Group Alpha", "Group Omega"}

    # A dead stage link loses that stage's standings, not the event.
    stage_url = "https://www.vlr.gg/event/2863/vct-2026-emea-stage-1/group-stage"
    with patch("httpx2.AsyncClient.get", side_effect=http_get(pages, failures={stage_url: 404})):
        degraded = await events.get_event_by_id("2863")

    assert (degraded.title, degraded.teams, degraded.standings) == (result.title, result.teams, [])


async def _bracket_sessions(monkeypatch, make_database):
    """Create a migrated scratch database and make it the application's session factory."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core import connections
    from app.db.migrations import upgrade_to_head

    database_url = make_database()
    await upgrade_to_head(database_url)
    sessions = async_sessionmaker(create_async_engine(database_url, poolclass=NullPool), expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    return sessions


def _enable_brackets(monkeypatch):
    """Turn Riot web brackets on with a key, plus the cache the bracket route writes through."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "RIOT_WEB_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ENABLE_CACHE", True)


def _riot_series(riot_id, *, vlr_match_id, event_id, tournament_id, start_time):
    """Build a stored Riot series linked to a VLR match."""
    from app.db.models import RiotWebMatch

    return RiotWebMatch(
        riot_id=riot_id,
        start_time=start_time,
        league_name="Test League",
        teams=[],
        best_of=3,
        vlr_match_id=vlr_match_id,
        vlr_event_id=event_id,
        tournament_id=tournament_id,
    )


def _vlr_team(team_id, name, tag, *, riot_name=None, riot_code=None):
    """Build a VLR team carrying the Riot fields the logo mirror writes when it matches one."""
    from app.db.models import Team

    now = int(datetime.now(UTC).timestamp())
    return Team(
        id=team_id,
        source="vlr",
        name=name,
        tag=tag,
        riot_name=riot_name,
        riot_code=riot_code,
        first_seen_at=now,
        last_fetched_at=now,
    )


@pytest.mark.asyncio
async def test_event_brackets_follow_riot_layout(monkeypatch, make_database):
    """Riot's column/cell order and per-slot origins reach the app; a section without edges still renders."""
    import httpx2

    from app.enums import BracketSourceKind, BracketSourceResult, BracketStatus, MatchStatus
    from app.services import brackets

    sessions = await _bracket_sessions(monkeypatch, make_database)
    _enable_brackets(monkeypatch)
    start = datetime(2024, 3, 16, 15, tzinfo=UTC)
    async with sessions.begin() as session:
        session.add_all(
            [
                _riot_series(
                    "112019385324229017",
                    vlr_match_id="1234",
                    event_id="2863",
                    tournament_id=BRACKETS_TOURNAMENT,
                    start_time=start,
                ),
                # The logo mirror matched these two Riot teams to VLR teams; Gen.G stays unmapped.
                _vlr_team("624", "Paper Rex", "PRX", riot_name="Paper Rex", riot_code="PRX"),
                _vlr_team("2357", "Sentinels", "SEN", riot_name="Sentinels", riot_code="SEN"),
            ]
        )

    payload = json.loads((RIOT_FIXTURE_DIR / "bracket_mastersmadrid2024_standings.json").read_text())
    # Riot's shape for a final that has not started: team id "0" is the TBD marker, and the origin
    # still names the match whose winner advances.
    finals = payload["data"]["standings"][0]["stages"][1]["sections"][0]["columns"][3]["cells"][0]["matches"][0]
    finals["state"] = "unstarted"
    for team in finals["teams"]:
        team.update(
            id="0",
            slug="tbd",
            name="TBD",
            code="TBD",
            image="https://static.lolesports.com/teams/team-tbd.png",
            result=None,
        )
    requested = []

    async def router(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        requested.append(request)
        return httpx2.Response(200, json=payload, request=request)

    redis = AsyncMock()
    redis.get.return_value = None
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        result = await brackets.event_brackets("2863", redis)

    assert result.event_id == "2863"
    assert [(section.tournament, section.stage, section.name) for section in result.sections] == [
        ("Masters Madrid", "Swiss", "Swiss"),
        ("Masters Madrid", "Playoffs", "Playoffs"),
    ]
    # A Swiss section has decision-point origins only: no progression edge, still rendered.
    swiss = result.sections[0]
    assert all(
        slot.source.kind == BracketSourceKind.SEED
        for column in swiss.columns
        for cell in column.cells
        for match in cell.matches
        for slot in match.slots
    )
    # A decision-point origin is a 1-based seed, not a progression edge.
    seed = swiss.columns[0].cells[0].matches[0].slots[0].source
    assert (seed.kind, seed.seed) == (BracketSourceKind.SEED, 2)
    playoffs = result.sections[1]
    assert [[cell.name for cell in column.cells] for column in playoffs.columns] == [
        ["Round 1"],
        ["Upper Bracket - Finals", "Lower Bracket - Semifinals"],
        ["Lower Bracket - Finals"],
        ["Finals"],
    ]
    linked = playoffs.columns[0].cells[0].matches[0]
    assert (linked.id, linked.vlr_match_id, linked.start_time, linked.best_of) == (
        "112019385324229017",
        "1234",
        start,
        3,
    )
    lower_final = playoffs.columns[2].cells[0].matches[0]
    assert (lower_final.id, lower_final.label, lower_final.status) == (
        "112019385324229033",
        "M15",
        MatchStatus.COMPLETED,
    )
    sentinels, paper_rex = lower_final.slots
    assert (sentinels.team.name, sentinels.score, sentinels.winner) == ("Sentinels", 3, True)
    assert (sentinels.source.kind, sentinels.source.match_id, sentinels.source.result) == (
        BracketSourceKind.MATCH,
        "112019385324229025",
        BracketSourceResult.LOSER,
    )
    assert (paper_rex.team.name, paper_rex.score, paper_rex.winner) == ("Paper Rex", 1, False)
    assert (paper_rex.source.match_id, paper_rex.source.result) == (
        "112019385324229029",
        BracketSourceResult.WINNER,
    )
    assert (result.available, result.status) == (True, BracketStatus.OK)
    assert result.fetched_at is not None
    # Riot ids are not VLR ids: only a mirrored Riot team gets one, and an unmapped one stays null.
    assert (sentinels.team.vlr_team_id, paper_rex.team.vlr_team_id) == ("2357", "624")
    # Riot pads some names; the display name is stripped.
    gen_g = linked.slots[1].team
    assert (gen_g.name, gen_g.vlr_team_id) == ("Gen.G", None)
    # A TBD slot has no team, score or winner, but still says which match winner it waits for.
    upcoming = playoffs.columns[3].cells[0].matches[0]
    assert (upcoming.id, upcoming.label, upcoming.status) == ("112019385324229039", "M16", MatchStatus.UPCOMING)
    assert all(slot.team is None and slot.score is None and slot.winner is None for slot in upcoming.slots)
    assert [(slot.source.kind, slot.source.match_id, slot.source.result) for slot in upcoming.slots] == [
        (BracketSourceKind.MATCH, "112019385324229025", BracketSourceResult.WINNER),
        (BracketSourceKind.MATCH, "112019385324229033", BracketSourceResult.WINNER),
    ]
    assert len(requested) == 1
    request = requested[0]
    assert (request.url.path, request.url.params["tournamentId"]) == (
        "/persisted/val/getStandings",
        BRACKETS_TOURNAMENT,
    )
    assert (request.url.params["hl"], request.url.params["sport"]) == ("en-US", "val")
    assert request.headers["x-api-key"] == "test-key"
    assert redis.set.call_args.args[0] == "valesports:riot_web:brackets:2863"
    assert redis.set.call_args.args[2] == 60

    # The fetched layout was stored: the next read converts it without asking Riot again.
    requested.clear()
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        stored = await brackets.event_brackets("2863", redis)
    assert stored == result and requested == []


def test_event_brackets_endpoint_degrades_to_empty_sections(monkeypatch, make_database):
    """A mapping gap, an unreadable stored layout and a Riot 502 answer 200 with what the app needs.

    An unreadable stored layout is refetched, so its Riot 502 degrades like the others; a readable
    layout with no bracket section is a different answer, and the response must say which is which.
    """
    import asyncio

    import httpx2
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.v1.endpoints.events import router
    from app.db.models import RiotWebTournament

    sessions = asyncio.run(_bracket_sessions(monkeypatch, make_database))
    _enable_brackets(monkeypatch)
    now = datetime.now(UTC)

    async def seed():
        async with sessions.begin() as session:
            session.add(
                _riot_series(
                    "112019385324229017",
                    vlr_match_id="1234",
                    event_id="2863",
                    tournament_id=BRACKETS_TOURNAMENT,
                    start_time=now,
                )
            )
            session.add(
                RiotWebTournament(
                    tournament_id=BRACKETS_TOURNAMENT,
                    standings={"id": BRACKETS_TOURNAMENT, "name": "Broken", "slug": "broken"},
                    fetched_at=now,
                )
            )
            session.add(
                _riot_series(
                    "112019385324229018",
                    vlr_match_id="1235",
                    event_id="3000",
                    tournament_id="112019354266558217",
                    start_time=now,
                )
            )
            # A readable tournament whose only sections are group tables VLR standings already cover.
            session.add(
                _riot_series(
                    "112019385324229019",
                    vlr_match_id="1236",
                    event_id="3100",
                    tournament_id="112019354266558219",
                    start_time=now,
                )
            )
            session.add(
                RiotWebTournament(
                    tournament_id="112019354266558219",
                    standings={
                        "id": "112019354266558219",
                        "name": "Groups Only",
                        "slug": "groups-only",
                        "stages": [
                            {
                                "name": "Regular Season",
                                "sections": [
                                    {"id": "112019376623574790", "name": "Group A", "type": "group", "columns": []}
                                ],
                            }
                        ],
                    },
                    fetched_at=now,
                )
            )

    asyncio.run(seed())

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/events")
    redis = AsyncMock()
    redis.get.return_value = None
    app.dependency_overrides[deps.get_redis_client] = lambda: redis

    requested = []

    async def router_http(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        requested.append(request)
        return httpx2.Response(502, json={"errors": [{"message": "Bad Gateway"}]}, request=request)

    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router_http), TestClient(app) as client:
        unreadable = client.get("/api/v1/events/2863/brackets")
        unmapped = client.get("/api/v1/events/9999/brackets")
        failing = client.get("/api/v1/events/3000/brackets")
        groups_only = client.get("/api/v1/events/3100/brackets")

    assert (unreadable.status_code, unmapped.status_code, failing.status_code, groups_only.status_code) == (
        200,
        200,
        200,
        200,
    )
    assert unreadable.json() == {
        "event_id": "2863",
        "sections": [],
        "available": False,
        "status": "unavailable",
        "fetched_at": None,
    }
    assert unmapped.json() == {
        "event_id": "9999",
        "sections": [],
        "available": False,
        "status": "unavailable",
        "fetched_at": None,
    }
    assert failing.json() == {
        "event_id": "3000",
        "sections": [],
        "available": False,
        "status": "unavailable",
        "fetched_at": None,
    }
    assert groups_only.json() == {
        "event_id": "3100",
        "sections": [],
        "available": False,
        "status": "no_bracket",
        "fetched_at": None,
    }
    # The unreadable stored layout is refetched, and the event with no stored layout asks Riot too;
    # the unmapped event has no tournament to ask about, and the readable layout is not refetched.
    assert sorted(request.url.params["tournamentId"] for request in requested) == sorted(
        [BRACKETS_TOURNAMENT, "112019354266558217"]
    )


@pytest.mark.asyncio
async def test_event_brackets_shared_tournament_returns_only_the_requested_event(monkeypatch, make_database):
    """One Riot tournament can cover two VLR events; each event must see only its own sections."""
    import httpx2

    from app.services import brackets

    sessions = await _bracket_sessions(monkeypatch, make_database)
    _enable_brackets(monkeypatch)
    start = datetime(2026, 7, 1, 1, tzinfo=UTC)
    tournament_id = "116435650052832023"
    north_match, south_match = "116435650058337174", "116435650058337204"
    async with sessions.begin() as session:
        session.add_all(
            [
                _riot_series(
                    north_match,
                    vlr_match_id="705814",
                    event_id="2944",
                    tournament_id=tournament_id,
                    start_time=start,
                ),
                _riot_series(
                    south_match,
                    vlr_match_id="705838",
                    event_id="2943",
                    tournament_id=tournament_id,
                    start_time=start,
                ),
            ]
        )

    payload = json.loads((RIOT_FIXTURE_DIR / "bracket_gc_latam_split2_2026_standings.json").read_text())
    requested = []

    async def router(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        requested.append(request)
        return httpx2.Response(200, json=payload, request=request)

    redis = AsyncMock()
    redis.get.return_value = None
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        north = await brackets.event_brackets("2944", redis)
        south = await brackets.event_brackets("2943", redis)

    assert [section.id for section in north.sections] == ["116435650057878300"]
    assert south_match not in north.model_dump_json()
    assert [section.id for section in south.sections] == ["116435650057878301"]
    assert north_match not in south.model_dump_json()
    # Unlinked matches stay in their section, and a linked one still resolves to its VLR match.
    assert north.sections[0].columns[0].cells[0].matches[0].vlr_match_id == "705814"
    assert [request.url.params["tournamentId"] for request in requested] == [tournament_id]


@pytest.mark.asyncio
async def test_event_brackets_withhold_a_section_no_link_claims(monkeypatch, make_database):
    """A shared tournament's unlinked section is nobody's until a link claims it.

    Only the north section is linked. A group-stage series maps the south event to the same
    tournament, so each request reaches the layout and must still keep out of the other's bracket.
    """
    import httpx2

    from app.enums import BracketStatus
    from app.services import brackets

    sessions = await _bracket_sessions(monkeypatch, make_database)
    _enable_brackets(monkeypatch)
    start = datetime(2026, 7, 1, 1, tzinfo=UTC)
    tournament_id = "116435650052832023"
    async with sessions.begin() as session:
        session.add_all(
            [
                _riot_series(
                    "116435650058337174",
                    vlr_match_id="705814",
                    event_id="2944",
                    tournament_id=tournament_id,
                    start_time=start,
                ),
                # A group-stage series of the same tournament maps South; no South bracket series is linked.
                _riot_series(
                    "116435650059999999",
                    vlr_match_id="705839",
                    event_id="2943",
                    tournament_id=tournament_id,
                    start_time=start,
                ),
            ]
        )

    payload = json.loads((RIOT_FIXTURE_DIR / "bracket_gc_latam_split2_2026_standings.json").read_text())

    async def router(client, url, *, params=None, **_kwargs):
        return httpx2.Response(200, json=payload, request=client.build_request("GET", url, params=params))

    redis = AsyncMock()
    redis.get.return_value = None
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        north = await brackets.event_brackets("2944", redis)
        south = await brackets.event_brackets("2943", redis)

    assert [section.id for section in north.sections] == ["116435650057878300"]
    assert "116435650057878301" not in north.model_dump_json()
    assert south.sections == []
    assert (north.available, north.status) == (True, BracketStatus.OK)
    assert (south.available, south.status) == (False, BracketStatus.NO_BRACKET)


@pytest.mark.asyncio
async def test_event_brackets_withhold_a_section_whose_links_span_two_events(monkeypatch, make_database):
    """A section whose linked matches name two VLR events is ambiguous, so neither event is served it."""
    import httpx2

    from app.services import brackets

    sessions = await _bracket_sessions(monkeypatch, make_database)
    _enable_brackets(monkeypatch)
    start = datetime(2026, 7, 1, 1, tzinfo=UTC)
    tournament_id = "116435650052832023"
    async with sessions.begin() as session:
        session.add_all(
            [
                _riot_series(
                    "116435650058337174",
                    vlr_match_id="705814",
                    event_id="2944",
                    tournament_id=tournament_id,
                    start_time=start,
                ),
                # Two matches in the same Riot section disagree about their VLR event.
                _riot_series(
                    "116435650058337178",
                    vlr_match_id="705815",
                    event_id="2943",
                    tournament_id=tournament_id,
                    start_time=start,
                ),
                _riot_series(
                    "116435650058337204",
                    vlr_match_id="705838",
                    event_id="2943",
                    tournament_id=tournament_id,
                    start_time=start,
                ),
            ]
        )

    payload = json.loads((RIOT_FIXTURE_DIR / "bracket_gc_latam_split2_2026_standings.json").read_text())

    async def router(client, url, *, params=None, **_kwargs):
        return httpx2.Response(200, json=payload, request=client.build_request("GET", url, params=params))

    redis = AsyncMock()
    redis.get.return_value = None
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        north = await brackets.event_brackets("2944", redis)
        south = await brackets.event_brackets("2943", redis)

    # North's ambiguous section is withheld even though one of its matches names North, and South
    # keeps only its own unambiguous section.
    assert north.sections == []
    assert [section.id for section in south.sections] == ["116435650057878301"]


@pytest.mark.asyncio
async def test_event_brackets_mark_an_incomplete_answer_stale(monkeypatch, make_database):
    """A stored layout is served when another mapped tournament cannot be fetched, but it is not "ok".

    The app must not present a bracket missing one tournament as a complete one, and it must still
    keep the sections it has instead of failing the route.
    """
    import httpx2

    from app.db.models import RiotWebTournament
    from app.enums import BracketStatus
    from app.services import brackets

    sessions = await _bracket_sessions(monkeypatch, make_database)
    _enable_brackets(monkeypatch)
    stored_at = datetime(2024, 3, 16, 15, tzinfo=UTC)
    payload = json.loads((RIOT_FIXTURE_DIR / "bracket_mastersmadrid2024_standings.json").read_text())
    async with sessions.begin() as session:
        session.add_all(
            [
                _riot_series(
                    "112019385324229017",
                    vlr_match_id="1234",
                    event_id="2863",
                    tournament_id=BRACKETS_TOURNAMENT,
                    start_time=stored_at,
                ),
                # A second tournament of the same event has no stored layout, so it asks Riot.
                _riot_series(
                    "112019385324229020",
                    vlr_match_id="1237",
                    event_id="2863",
                    tournament_id="112019354266558217",
                    start_time=stored_at,
                ),
                RiotWebTournament(
                    tournament_id=BRACKETS_TOURNAMENT,
                    standings=payload["data"]["standings"][0],
                    fetched_at=stored_at,
                ),
            ]
        )

    async def router(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        return httpx2.Response(502, json={"errors": [{"message": "Bad Gateway"}]}, request=request)

    redis = AsyncMock()
    redis.get.return_value = None
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        result = await brackets.event_brackets("2863", redis)

    assert [section.name for section in result.sections] == ["Swiss", "Playoffs"]
    assert (result.available, result.status) == (True, BracketStatus.STALE)
    assert result.fetched_at == stored_at


@pytest.mark.asyncio
async def test_event_brackets_skip_only_a_malformed_section(monkeypatch, make_database):
    """Corrupting one Riot section must cost that section, not the tournament's healthy brackets."""
    import httpx2

    from app.services import brackets

    sessions = await _bracket_sessions(monkeypatch, make_database)
    _enable_brackets(monkeypatch)
    start = datetime(2024, 3, 16, 15, tzinfo=UTC)
    async with sessions.begin() as session:
        session.add(
            _riot_series(
                "112019385324229017",
                vlr_match_id="1234",
                event_id="2863",
                tournament_id=BRACKETS_TOURNAMENT,
                start_time=start,
            )
        )

    payload = json.loads((RIOT_FIXTURE_DIR / "bracket_mastersmadrid2024_standings.json").read_text())
    swiss = next(
        section
        for stage in payload["data"]["standings"][0]["stages"]
        for section in stage["sections"]
        if section["id"] == "112019376623574799"
    )
    swiss["columns"][0]["cells"][0]["matches"][0]["teams"] = []
    requested = []

    async def router(client, url, *, params=None, **_kwargs):
        request = client.build_request("GET", url, params=params)
        requested.append(request)
        return httpx2.Response(200, json=payload, request=request)

    redis = AsyncMock()
    redis.get.return_value = None
    with patch("httpx2.AsyncClient.get", autospec=True, side_effect=router):
        result = await brackets.event_brackets("2863", redis)
        requested.clear()
        stored = await brackets.event_brackets("2863", redis)

    assert [section.name for section in result.sections] == ["Playoffs"]
    # Only the readable layout was stored, so the next read renders it without asking Riot again.
    assert stored == result and requested == []
