import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from alembic import command
from app import schemas
from app.api.v2.api import router
from app.constants import ELO_ALGORITHM, ELO_BASE, ELO_K, ELO_MAP_ALPHA, Circuit, MatchStatus, RankingScope, Region
from app.core import connections
from app.core.config import settings
from app.cron.team_rankings import team_rankings_cron
from app.db.engine import create_engine
from app.db.migrations import upgrade_to_head
from app.db.models import EventRecord, MapRecord, MatchRecord, RankingResult, Team, TeamCircuit, TeamElo
from app.schemas.matches import MatchData
from app.schemas.matches import Team as MapTeam
from app.services import matches, ranking_store, team_rankings
from app.services.ranking_store import StoredMap, StoredMatch

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# The fixture is Team A 2-1 Team B with one played map (Lotus 13-10), played 2025-10-01.
DETAIL_MATCH_IDS = [str(match_id) for match_id in range(12345, 12350)]


@pytest.fixture
def ranking_sessions(monkeypatch, tmp_path):
    """Yield a session factory over a migrated scratch database, wired into the app session dep."""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}"
    asyncio.run(upgrade_to_head(database_url))
    engine = create_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(connections, "subscription_sessions", sessions)
    yield sessions


async def _details(http_response) -> schemas.MatchWithDetails:
    """Parse the shared match fixture through the VLR service boundary."""
    fixture = (FIXTURE_DIR / "match_12345.html").read_bytes()
    response = http_response("https://www.vlr.gg/12345", fixture)

    with patch("httpx2.AsyncClient.get", return_value=response):
        return await matches.match_by_id("12345", None)


async def _session_factory(tmp_path, name: str):
    """Create a migrated scratch database and return its session factory."""
    database_url = f"sqlite+aiosqlite:///{tmp_path / name}"
    await upgrade_to_head(database_url)
    return async_sessionmaker(create_engine(database_url), expire_on_commit=False)


async def _seed_ledger(sessions, stored) -> None:
    """Store raw matches in the given order, then rebuild the ratings from them."""
    async with sessions.begin() as session:
        session.add(EventRecord(id="10", name="Event", circuit=Circuit.VCT))
        session.add_all(
            Team(id=team_id, source="test", first_seen_at=0, last_fetched_at=0) for team_id in ("1", "2", "3")
        )
        await session.flush()
        for match_id, played_on, team_a_id, team_b_id, score_a, score_b, map_winners in stored:
            session.add(
                MatchRecord(
                    id=match_id,
                    event_id="10",
                    stage=None,
                    played_on=played_on,
                    team_a_id=team_a_id,
                    team_b_id=team_b_id,
                    team_a_score=score_a,
                    team_b_score=score_b,
                    patch=None,
                    source="test",
                    ingested_at=0,
                )
            )
            await session.flush()
            session.add_all(
                MapRecord(
                    match_id=match_id,
                    map_index=index,
                    name="Map",
                    team_a_score=13 if team_a_won else 7,
                    team_b_score=7 if team_a_won else 13,
                )
                for index, team_a_won in enumerate(map_winners)
            )
    async with sessions.begin() as session:
        await team_rankings.rebuild_ratings(session)


async def _stored_ratings(sessions) -> dict[str, tuple]:
    """Read every team's stored ratings and record counts."""
    async with sessions() as session:
        rows = (await session.execute(select(TeamElo))).scalars()
        return {
            row.team_id: (row.match_elo, row.map_elo, row.matches, row.match_wins, row.maps, row.map_wins)
            for row in rows
        }


def _map_data(number: int, name: str, score_a: int, score_b: int) -> MatchData:
    """Build one parsed map row of a synthetic complete match page."""
    return MatchData(
        number=number,
        map=name,
        teams=[MapTeam(name="Team A", score=score_a), MapTeam(name="Team B", score=score_b)],
        members=[],
        rounds=[],
    )


def test_same_day_results_share_the_pre_day_rating_and_blend_map_share():
    # Two same-day wins for team 1 both use its pre-day rating, not the first result's update.
    ratings: dict[str, float] = {}
    updates = team_rankings.apply_day(ratings, [("1", "2", 1.0), ("1", "3", 1.0)])
    assert [update.delta for update in updates] == [ELO_K / 2, ELO_K / 2]
    assert updates[1].team_a_before == ELO_BASE
    assert ratings == {"1": ELO_BASE + ELO_K, "2": ELO_BASE - ELO_K / 2, "3": ELO_BASE - ELO_K / 2}
    assert team_rankings.expected(ELO_BASE, ELO_BASE) == 0.5

    # A complete 2-1 map score contributes its share with weight 0.3...
    match = StoredMatch(
        match_id="1",
        event_id="10",
        event_name="Event",
        played_on=date(2026, 10, 1),
        circuit=Circuit.VCT,
        team_a_id="1",
        team_b_id="2",
        team_a_score=2,
        team_b_score=1,
        maps=(StoredMap(0, True), StoredMap(1, False), StoredMap(2, True)),
    )
    elos = {row["team_id"]: row for row in team_rankings.replay_ratings([match]).elos}
    observed = (1 - ELO_MAP_ALPHA) + ELO_MAP_ALPHA * 2 / 3
    assert elos["1"]["match_elo"] == pytest.approx(ELO_BASE + ELO_K * (observed - 0.5))
    assert elos["1"]["map_elo"] == ELO_BASE + ELO_K / 2

    # ...while incomplete stored maps fall back to the binary series outcome.
    incomplete = replace(match, maps=(StoredMap(0, True),))
    elos = {row["team_id"]: row for row in team_rankings.replay_ratings([incomplete]).elos}
    assert elos["1"]["match_elo"] == ELO_BASE + ELO_K / 2


@pytest.mark.parametrize(
    ("title", "region"),
    [
        ("VALORANT Champions Tour 2025: Americas", Region.AMERICAS),
        ("Challengers 2025: North America", Region.AMERICAS),
        ("Challengers 2025: NA", Region.AMERICAS),
        ("Game Changers 2025: Brazil", Region.AMERICAS),
        ("Challengers 2025: EMEA", Region.EMEA),
        ("Challengers 2025: Türkiye", Region.EMEA),
        ("Challengers 2025: North//East", Region.EMEA),
        ("Challengers 2025: UK", Region.EMEA),
        ("Challengers 2025: Polaris", Region.EMEA),
        ("Challengers 2025: Southeast Asia", Region.PACIFIC),
        ("Challengers 2025: SEA", Region.PACIFIC),
        ("Challengers 2025: India", Region.PACIFIC),
        ("Challengers 2025: Taiwan/Hong Kong", Region.PACIFIC),
        ("Challengers 2025: TW/HK", Region.PACIFIC),
        ("VALORANT Champions Tour 2025: China", Region.CHINA),
        ("Champions Tour 2025: Masters", None),
    ],
)
def test_event_region_matches_regional_event_titles(title, region):
    # A title declares its league or sub-region; a Masters title declares none.
    assert team_rankings.event_region(title) is region


def test_replay_assigns_region_from_recent_tiered_events():
    start = date(2025, 1, 1)

    def match(day: int, team_id: str, event_id: str, title: str, circuit: Circuit = Circuit.VCT) -> StoredMatch:
        return StoredMatch(
            match_id=f"{day}-{event_id}",
            event_id=event_id,
            event_name=title,
            played_on=start + timedelta(days=day),
            circuit=circuit,
            team_a_id=team_id,
            team_b_id="99",
            team_a_score=1,
            team_b_score=0,
            maps=(),
        )

    matches = [
        # Team 1: eleven old EMEA events outvote ten recent Americas events overall,
        # but only the ten most recent events vote.
        *(match(day, "1", f"e{day}", "Champions Tour 2025: EMEA") for day in range(1, 12)),
        *(match(day, "1", f"e{day}", "Challengers 2025: Americas") for day in range(12, 22)),
        # Team 2: an offseason title names China, but only tiered circuits vote.
        match(1, "2", "e20", "Challengers 2025: Americas"),
        match(2, "2", "e21", "Challengers 2025: EMEA"),
        match(3, "2", "e22", "Challengers 2025: Americas"),
        *(match(day, "2", f"e2{day}", "Champions Tour 2025: China", Circuit.OFFSEASON) for day in range(4, 7)),
        # Team 3's only tiered event declares no region.
        match(1, "3", "e30", "Champions Tour 2025: Masters"),
        # Team 4 is split 1-1, so its most recent event wins the tie.
        match(1, "4", "e40", "Challengers 2025: Pacific", Circuit.VCL),
        match(2, "4", "e41", "Challengers 2025: EMEA", Circuit.VCL),
        # Team 5 played one EMEA event three times and two Americas events once each;
        # distinct events vote, not matches.
        *(match(day, "5", "e50", "Champions Tour 2025: EMEA") for day in range(1, 4)),
        match(4, "5", "e51", "Champions Tour 2025: Americas"),
        match(5, "5", "e52", "Challengers 2025: Brazil", Circuit.VCL),
    ]

    elos = {row["team_id"]: row for row in team_rankings.replay_ratings(matches).elos}

    assert elos["1"]["region"] is Region.AMERICAS
    assert elos["2"]["region"] is Region.AMERICAS
    assert elos["3"]["region"] is None
    assert elos["4"]["region"] is Region.EMEA
    assert elos["5"]["region"] is Region.AMERICAS


def _team_elo_columns(path: Path) -> set[str]:
    """Read the team_elo column names from a migrated scratch database."""
    with sqlite3.connect(path) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(team_elo)")}


def test_region_migration_adds_and_removes_the_column(tmp_path):
    # A rolled-back deploy must restore the pre-region schema, so the migration
    # drops the column again and re-running the upgrade adds it back. The
    # revisions are pinned so a later migration cannot change what this asserts.
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'migration.sqlite3'}"
    path = tmp_path / "migration.sqlite3"
    config = Config("alembic.ini")
    config.attributes["app"] = True
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "d94cce987756")
    assert "region" in _team_elo_columns(path)
    command.downgrade(config, "f7a8805679f3")
    assert "region" not in _team_elo_columns(path)
    command.upgrade(config, "d94cce987756")
    assert "region" in _team_elo_columns(path)


@pytest.mark.asyncio
async def test_rebuild_ratings_is_order_independent(tmp_path):
    # The same ledger seeded newest-first and oldest-first must rate identically.
    stored = [
        ("101", date(2025, 10, 1), "1", "2", 2, 0, (True, True)),
        ("102", date(2025, 10, 1), "3", "1", 2, 1, (True, False, True)),
        ("103", date(2025, 10, 2), "2", "3", 1, 2, (True, False, False)),
    ]
    chronological = await _session_factory(tmp_path, "chronological.sqlite3")
    await _seed_ledger(chronological, stored)
    reversed_arrival = await _session_factory(tmp_path, "reversed.sqlite3")
    await _seed_ledger(reversed_arrival, list(reversed(stored)))

    ratings = await _stored_ratings(chronological)
    assert ratings == await _stored_ratings(reversed_arrival)
    assert {team_id: row[2] for team_id, row in ratings.items()} == {"1": 2, "2": 2, "3": 2}


@pytest.mark.asyncio
async def test_ingest_match_writes_ledger_and_rating_state(ranking_sessions, http_response):
    details = await _details(http_response)
    async with ranking_sessions.begin() as session:
        assert await team_rankings.ingest_match(session, "12345", details, Circuit.VCT) is True

    async with ranking_sessions() as session:
        teams = {team.id: team for team in (await session.execute(select(Team))).scalars()}
        assert (teams["1"].name, teams["1"].tag, teams["2"].name) == ("Team A", "A", "Team B")
        match = await session.get(MatchRecord, "12345")
        assert (match.played_on, match.stage, match.patch, match.source) == (date(2025, 10, 1), "Stage", "13.05", "vlr")
        assert (match.team_a_id, match.team_a_score, match.team_b_score) == ("1", 2, 1)
        maps = list((await session.execute(select(MapRecord).order_by(MapRecord.map_index))).scalars())
        assert [(item.map_index, item.name, item.team_a_score, item.team_b_score) for item in maps] == [
            (0, "Lotus", 13, 10)
        ]

    async with ranking_sessions.begin() as session:
        await team_rankings.rebuild_ratings(session)

    async with ranking_sessions() as session:
        winner = await session.get(TeamElo, "1")
        assert (winner.match_elo, winner.map_elo) == (1524.0, 1524.0)
        assert (winner.matches, winner.match_wins, winner.maps, winner.map_wins) == (1, 1, 1, 1)
        assert winner.last_played_on == date(2025, 10, 1)
        assert (await session.get(TeamElo, "2")).match_elo == 1476.0
        results = list((await session.execute(select(RankingResult).order_by(RankingResult.seq))).scalars())
        assert [(row.scope, row.map_index, row.team_a_elo_before, row.team_a_delta) for row in results] == [
            (RankingScope.MATCH, -1, 1500.0, 24.0),
            (RankingScope.MAP, 0, 1500.0, 24.0),
        ]

    # Re-ingesting the same match is a no-op, so the ledger never double counts a result.
    async with ranking_sessions.begin() as session:
        assert await team_rankings.ingest_match(session, "12345", details, Circuit.VCT) is False
    async with ranking_sessions() as session:
        assert len(list((await session.execute(select(RankingResult))).scalars())) == 2


@pytest.mark.asyncio
async def test_ingest_match_repairs_an_incomplete_match_and_rebuilds_ratings(ranking_sessions, http_response):
    details = await _details(http_response)
    async with ranking_sessions.begin() as session:
        await ranking_store.upsert_event(session, details.event.id, details.event.series, Circuit.VCT)
        session.add(
            MatchRecord(
                id="12345",
                event_id=details.event.id,
                stage=None,
                played_on=date(2025, 10, 1),
                team_a_id=None,
                team_b_id=None,
                team_a_score=None,
                team_b_score=None,
                patch=None,
                source="vlr",
                ingested_at=0,
            )
        )
        await session.flush()
        # An earlier fetch stored map_index 1 and 2 of the 2-1 series, but not its first map.
        session.add_all(
            [
                MapRecord(match_id="12345", map_index=1, name="Haven", team_a_score=10, team_b_score=13),
                MapRecord(match_id="12345", map_index=2, name="Split", team_a_score=13, team_b_score=7),
            ]
        )
    listed = schemas.Match(
        id="12345",
        team1=schemas.MatchTeam(id="1", name="Team A", score=2),
        team2=schemas.MatchTeam(id="2", name="Team B", score=1),
        status=MatchStatus.COMPLETED,
        time=datetime(2025, 10, 1, 12, tzinfo=UTC),
        event="Event",
        series="Stage",
    )
    async with ranking_sessions() as session:
        stored = (await ranking_store.stored_listings(session))["12345"]
        assert stored.complete is False
        assert team_rankings.needs_repair(listed, stored) is True
        # An incomplete fetch waits out the repair cooldown before it is fetched again...
        fetched = replace(stored, ingested_at=int(datetime(2025, 10, 1, 12, tzinfo=UTC).timestamp()))
        assert (
            team_rankings.needs_repair(listed, fetched, now=int(datetime(2025, 10, 1, 13, tzinfo=UTC).timestamp()))
            is False
        )
        assert (
            team_rankings.needs_repair(listed, fetched, now=int(datetime(2025, 10, 1, 17, tzinfo=UTC).timestamp()))
            is True
        )
        # ...while a match whose page never produced maps is abandoned after a week.
        ancient = replace(fetched, maps=0, played_on=date(2025, 9, 1))
        assert (
            team_rankings.needs_repair(listed, ancient, now=int(datetime(2025, 9, 15, 12, tzinfo=UTC).timestamp()))
            is False
        )

    async with ranking_sessions.begin() as session:
        assert await team_rankings.ingest_match(session, "12345", details, Circuit.VCT) is True
    async with ranking_sessions.begin() as session:
        await team_rankings.rebuild_ratings(session)

    async with ranking_sessions() as session:
        match = await session.get(MatchRecord, "12345")
        assert (match.team_a_id, match.team_b_id, match.team_a_score, match.team_b_score) == ("1", "2", 2, 1)
        # The repair page carries the first map only; the partial fetch must keep the two stored maps.
        maps = list((await session.execute(select(MapRecord).order_by(MapRecord.map_index))).scalars())
        assert [(item.map_index, item.team_a_score, item.team_b_score) for item in maps] == [
            (0, 13, 10),
            (1, 10, 13),
            (2, 13, 7),
        ]
        winner = await session.get(TeamElo, "1")
        assert (winner.matches, winner.match_wins, winner.maps, winner.map_wins) == (1, 1, 3, 2)
        assert (winner.match_elo, winner.map_elo) == pytest.approx((1519.2, 1524.0))
        stored = (await ranking_store.stored_listings(session))["12345"]
        assert stored.complete is True
        assert team_rankings.needs_repair(listed, stored) is False

    # A complete fetch, by contrast, prunes maps a shorter series no longer has.
    complete = details.model_copy(
        update={
            "data": [
                details.data[0],
                _map_data(2, "Haven", 10, 13),
                _map_data(3, "Split", 13, 7),
            ]
        }
    )
    async with ranking_sessions.begin() as session:
        session.add(MapRecord(match_id="12345", map_index=3, name="Ascent", team_a_score=13, team_b_score=7))
    async with ranking_sessions.begin() as session:
        assert await team_rankings.ingest_match(session, "12345", complete, Circuit.VCT) is True
    async with ranking_sessions() as session:
        maps = list((await session.execute(select(MapRecord).order_by(MapRecord.map_index))).scalars())
        assert [item.map_index for item in maps] == [0, 1, 2]
        stored = (await ranking_store.stored_listings(session))["12345"]
        assert stored.complete is True
        # A listing whose sides carry no IDs cannot contradict the stored one.
        unmapped = listed.model_copy(
            update={
                "team1": listed.team1.model_copy(update={"id": None}),
                "team2": listed.team2.model_copy(update={"id": None}),
            }
        )
        assert team_rankings.needs_repair(unmapped, stored) is False


@pytest.mark.asyncio
async def test_team_rankings_cron_rebuilds_a_ledger_left_stale_by_a_crash(ranking_sessions, monkeypatch):
    # A run that committed a complete match but died before its rebuild leaves the ledger missing it.
    async with ranking_sessions.begin() as session:
        session.add(EventRecord(id="10", name="Challengers 2025: EMEA", circuit=Circuit.VCT))
        session.add_all(Team(id=team_id, source="test", first_seen_at=0, last_fetched_at=0) for team_id in ("1", "2"))
        await session.flush()
        session.add(
            MatchRecord(
                id="12345",
                event_id="10",
                stage=None,
                played_on=date(2025, 10, 1),
                team_a_id="1",
                team_b_id="2",
                team_a_score=2,
                team_b_score=1,
                patch=None,
                source="vlr",
                ingested_at=0,
            )
        )
        await session.flush()
        session.add_all(
            MapRecord(match_id="12345", map_index=index, name="Map", team_a_score=score_a, team_b_score=score_b)
            for index, (score_a, score_b) in enumerate([(13, 7), (7, 13), (13, 9)])
        )
    listed = schemas.Match(
        id="12345",
        team1=schemas.MatchTeam(id="1", name="Team A", score=2),
        team2=schemas.MatchTeam(id="2", name="Team B", score=1),
        status=MatchStatus.COMPLETED,
        time=datetime(2025, 10, 1, 12, tzinfo=UTC),
        event="Event",
        series="Stage",
    )
    redis = AsyncMock()
    redis.get.return_value = schemas.MatchListAdapter.dump_json([listed])
    fetch = AsyncMock()
    monkeypatch.setattr(matches, "match_by_id", fetch)

    await team_rankings_cron({"redis": redis})

    # Nothing was pending and nothing was fetched, but the missing ledger was rebuilt once.
    fetch.assert_not_awaited()
    async with ranking_sessions() as session:
        assert len(list((await session.execute(select(RankingResult))).scalars())) == 4
        assert (await session.get(TeamElo, "1")).matches == 1
        assert await ranking_store.needs_rebuild(session) is False

    # A correction committed with the same row counts (the winner flipped) must still
    # rebuild: the counts alone cannot see it, only the stored outcomes can.
    async with ranking_sessions.begin() as session:
        corrected = await session.get(MatchRecord, "12345")
        corrected.team_a_score, corrected.team_b_score = 1, 2
    corrected_listing = listed.model_copy(
        update={
            "team1": listed.team1.model_copy(update={"score": 1}),
            "team2": listed.team2.model_copy(update={"score": 2}),
        }
    )
    redis.get.return_value = schemas.MatchListAdapter.dump_json([corrected_listing])
    async with ranking_sessions() as session:
        assert await ranking_store.needs_rebuild(session) is True

    await team_rankings_cron({"redis": redis})

    fetch.assert_not_awaited()
    async with ranking_sessions() as session:
        match_row = (
            await session.execute(select(RankingResult).where(RankingResult.scope == RankingScope.MATCH))
        ).scalar_one()
        assert match_row.winner_team_id == "2"
        team_b = await session.get(TeamElo, "2")
        assert (team_b.matches, team_b.match_wins, team_b.maps, team_b.map_wins) == (1, 1, 3, 1)
        assert await ranking_store.needs_rebuild(session) is False

    # A correction confined to one map leaves both the series winner and the counts
    # alone, so only the map outcomes can catch it.
    async with ranking_sessions.begin() as session:
        first_map = await session.get(MapRecord, ("12345", 0))
        first_map.team_a_score, first_map.team_b_score = 7, 13
    async with ranking_sessions() as session:
        assert await ranking_store.needs_rebuild(session) is True

    await team_rankings_cron({"redis": redis})

    fetch.assert_not_awaited()
    async with ranking_sessions() as session:
        map_rows = list(
            (
                await session.execute(
                    select(RankingResult)
                    .where(RankingResult.scope == RankingScope.MAP)
                    .order_by(RankingResult.map_index)
                )
            ).scalars()
        )
        assert [row.winner_team_id for row in map_rows] == ["2", "2", "1"]
        assert (await session.get(TeamElo, "2")).map_wins == 2
        assert await ranking_store.needs_rebuild(session) is False

    # Put the losing side on a sweep so swapping its ID below cannot move a winner.
    async with ranking_sessions.begin() as session:
        last_map = await session.get(MapRecord, ("12345", 2))
        last_map.team_a_score, last_map.team_b_score = 9, 13
    async with ranking_sessions() as session:
        assert await ranking_store.needs_rebuild(session) is True

    await team_rankings_cron({"redis": redis})

    fetch.assert_not_awaited()
    async with ranking_sessions() as session:
        sweep_winners = list(
            (
                await session.execute(
                    select(RankingResult.winner_team_id)
                    .where(RankingResult.scope == RankingScope.MAP)
                    .order_by(RankingResult.map_index)
                )
            ).scalars()
        )
        assert sweep_winners == ["2", "2", "2"]
        assert (await session.get(TeamElo, "2")).map_wins == 3

    # A correction that swaps an opponent while keeping the counts and every winner
    # alone is invisible to all of the above, so only the stored participants catch it.
    async with ranking_sessions.begin() as session:
        session.add(Team(id="3", source="test", first_seen_at=0, last_fetched_at=0))
        await session.flush()
        losing_side = await session.get(MatchRecord, "12345")
        losing_side.team_a_id = "3"
    corrected_listing = corrected_listing.model_copy(
        update={"team1": corrected_listing.team1.model_copy(update={"id": "3", "name": "Team C"})}
    )
    redis.get.return_value = schemas.MatchListAdapter.dump_json([corrected_listing])
    async with ranking_sessions() as session:
        assert await ranking_store.needs_rebuild(session) is True

    await team_rankings_cron({"redis": redis})

    fetch.assert_not_awaited()
    async with ranking_sessions() as session:
        match_row = (
            await session.execute(select(RankingResult).where(RankingResult.scope == RankingScope.MATCH))
        ).scalar_one()
        assert (match_row.team_a_id, match_row.team_b_id, match_row.winner_team_id) == ("3", "2", "2")
        assert await session.get(TeamElo, "1") is None
        team_b = await session.get(TeamElo, "2")
        assert (team_b.match_elo, team_b.map_elo) == (1524.0, 1572.0)
        assert (team_b.matches, team_b.match_wins, team_b.maps, team_b.map_wins) == (1, 1, 3, 3)
        team_c = await session.get(TeamElo, "3")
        assert (team_c.match_elo, team_c.map_elo) == (1476.0, 1428.0)
        assert (team_c.matches, team_c.match_wins, team_c.maps, team_c.map_wins) == (1, 0, 3, 0)
        assert await ranking_store.needs_rebuild(session) is False

        # The head-to-head follows the corrected participants, not the old ones.
        assert await ranking_store.head_to_head_results(session, "1", "2") == ([], [])
        h2h_matches, h2h_maps = await ranking_store.head_to_head_results(session, "3", "2")
        assert [(row.team_a_id, row.team_b_id, row.winner_team_id) for row in h2h_matches] == [("3", "2", "2")]
        assert [row.winner_team_id for row in sorted(h2h_maps, key=lambda row: row.map_index)] == ["2", "2", "2"]

    # A correction to the date alone leaves the counts, winners, and participants alone;
    # only the stored dates catch it, and the rebuild moves the ledger rows with it.
    async with ranking_sessions.begin() as session:
        moved = await session.get(MatchRecord, "12345")
        moved.played_on = date(2025, 10, 3)
    async with ranking_sessions() as session:
        assert await ranking_store.needs_rebuild(session) is True

    await team_rankings_cron({"redis": redis})

    fetch.assert_not_awaited()
    async with ranking_sessions() as session:
        assert set((await session.execute(select(RankingResult.played_on))).scalars()) == {date(2025, 10, 3)}
        assert await ranking_store.needs_rebuild(session) is False

    # An event assigned no circuit is looked up in the tier listings again once its
    # check has aged out; a listing that now carries it fixes the circuit and rebuilds.
    async with ranking_sessions.begin() as session:
        other = await session.get(EventRecord, "10")
        other.circuit = Circuit.OTHER
        other.circuit_checked_at = 0
    monkeypatch.setattr("app.cron.team_rankings.tier_event_circuits", AsyncMock(return_value={"10": Circuit.VCT}))

    await team_rankings_cron({"redis": redis})

    fetch.assert_not_awaited()
    async with ranking_sessions() as session:
        event = await session.get(EventRecord, "10")
        assert event.circuit == Circuit.VCT
        assert event.circuit_checked_at is not None and event.circuit_checked_at > 0
        circuits = list((await session.execute(select(TeamCircuit))).scalars())
        assert {(row.team_id, row.circuit) for row in circuits} == {("2", Circuit.VCT), ("3", Circuit.VCT)}


def test_rankings_api_serves_lists_profiles_and_predictions(monkeypatch, ranking_sessions, http_response):
    monkeypatch.setattr(team_rankings, "ranking_date", lambda: date(2025, 10, 2))

    async def seed():
        details = await _details(http_response)
        # Five wins in a row for Team A so the profile clears the default minimum-match floor.
        for match_id in DETAIL_MATCH_IDS:
            async with ranking_sessions.begin() as session:
                await team_rankings.ingest_match(session, match_id, details, Circuit.VCT)
        async with ranking_sessions.begin() as session:
            await team_rankings.rebuild_ratings(session)

    asyncio.run(seed())

    async def seed_stale_circuit():
        # Team B last played Game Changers well outside the 180-day window; that is history.
        async with ranking_sessions.begin() as session:
            session.add(TeamCircuit(team_id="2", circuit=Circuit.GC, matches=4, last_played_on=date(2025, 3, 1)))

    asyncio.run(seed_stale_circuit())

    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    client = TestClient(app)

    response = client.get("/api/v2/rankings/", params={"min_matches": 0, "include_inactive": True})
    assert response.status_code == 200
    listed = schemas.RankingListResponse.model_validate(response.json())
    assert (listed.as_of, listed.algorithm, listed.circuit, listed.total) == (
        date(2025, 10, 2),
        ELO_ALGORITHM,
        "all",
        2,
    )
    first, second = listed.teams
    assert (first.rank, first.overall_rank, first.team.id, first.team.name) == (1, 1, "1", "Team A")
    # Five same-day wins at K=48 move Team A up 120 points and Team B down the same,
    # and the published ratings are whole numbers rather than floats.
    won_elo, lost_elo = round(ELO_BASE + 5 * ELO_K / 2), round(ELO_BASE - 5 * ELO_K / 2)
    assert (first.elo, first.map_elo) == (won_elo, won_elo)
    assert (second.elo, second.map_elo) == (lost_elo, lost_elo)
    assert [row["elo"] for row in response.json()["teams"]] == [won_elo, lost_elo]
    assert isinstance(response.json()["teams"][0]["elo"], int)
    assert (first.matches.played, first.matches.wins, first.matches.losses, first.matches.win_rate) == (5, 5, 0, 1.0)
    assert (first.maps.played, first.maps.wins) == (5, 5)
    assert (first.primary_circuit, first.circuits, first.last_played_on) == (
        Circuit.VCT,
        [Circuit.VCT],
        date(2025, 10, 1),
    )
    assert second.matches.wins == 0

    # The circuit filter narrows the list; pagination slices it. Team B's stale Game
    # Changers membership only shows when inactive teams are requested.
    assert client.get("/api/v2/rankings/", params={"circuit": "gc"}).json()["total"] == 0
    stale_circuit = client.get("/api/v2/rankings/", params={"circuit": "gc", "include_inactive": True}).json()
    assert (stale_circuit["total"], stale_circuit["teams"][0]["team"]["id"]) == (1, "2")
    all_circuits = client.get(
        "/api/v2/rankings/", params={"circuit": "all", "min_matches": 0, "include_inactive": True}
    )
    assert all_circuits.status_code == 200
    assert all_circuits.json()["circuit"] == "all"
    assert all_circuits.json()["total"] == 2
    page = client.get("/api/v2/rankings/", params={"limit": 1, "offset": 1}).json()
    assert (page["total"], [row["team"]["id"] for row in page["teams"]]) == (2, ["2"])
    # Ordering by win rate ascending puts the winless team first.
    by_win_rate = client.get("/api/v2/rankings/", params={"sort": "win_rate", "order": "asc"}).json()
    assert [row["team"]["id"] for row in by_win_rate["teams"]] == ["2", "1"]

    profile = client.get("/api/v2/rankings/teams/1")
    assert profile.status_code == 200
    profile_data = profile.json()
    assert (profile_data["rank"], profile_data["circuit_rank"], profile_data["active"], profile_data["form"]) == (
        1,
        1,
        True,
        "WWWWW",
    )
    assert [item["match_id"] for item in profile_data["recent"]] == list(reversed(DETAIL_MATCH_IDS))
    assert profile_data["recent"][0]["opponent"]["id"] == "2"
    assert profile_data["recent"][0]["team_score"] == 2 and profile_data["recent"][0]["won"] is True

    prediction = client.get("/api/v2/rankings/predict", params={"team_a": "1", "team_b": "2"})
    assert prediction.status_code == 200
    predicted = prediction.json()
    assert predicted["match"]["team_a"] > 0.5 > predicted["match"]["team_b"]
    assert predicted["map"]["team_a"] > 0.5
    assert predicted["head_to_head"] == {
        "matches": 5,
        "team_a_wins": 5,
        "team_b_wins": 0,
        "maps": 5,
        "team_a_map_wins": 5,
        "team_b_map_wins": 0,
        "last_played_on": "2025-10-01",
    }

    # Unknown teams are 404; a team cannot be matched against itself, and circuits are enum-checked.
    assert client.get("/api/v2/rankings/teams/999").status_code == 404
    assert client.get("/api/v2/rankings/predict", params={"team_a": "1", "team_b": "1"}).status_code == 422
    assert client.get("/api/v2/rankings/", params={"circuit": "nope"}).status_code == 422

    # A configured prediction service supplies the series probability, called with its expected contract.
    monkeypatch.setattr(settings, "PREDICTION_SERVICE_URL", "http://predictions.test")
    post = AsyncMock(
        return_value=MagicMock(
            json=lambda: {
                "team_probabilities": [
                    {"team_id": "1", "probability": 0.75},
                    {"team_id": "2", "probability": 0.25},
                ]
            }
        )
    )
    monkeypatch.setattr(httpx2.AsyncClient, "post", post)
    external = client.get("/api/v2/rankings/predict", params={"team_a": "1", "team_b": "2"}).json()
    assert external["match"] == {"team_a": 0.75, "team_b": 0.25}
    assert post.call_args.args == ("http://predictions.test/predict",)
    assert post.call_args.kwargs["json"] == {
        "task": "match_win",
        "as_of": "2025-10-02",
        "team_a_id": "1",
        "team_b_id": "2",
    }

    # A malformed payload (a string probability) falls back to pure Elo instead of failing the request.
    post = AsyncMock(
        return_value=MagicMock(
            json=lambda: {
                "team_probabilities": [
                    {"team_id": "1", "probability": "0.75"},
                    {"team_id": "2", "probability": 0.25},
                ]
            }
        )
    )
    monkeypatch.setattr(httpx2.AsyncClient, "post", post)
    expected_a = team_rankings.expected(first.elo, second.elo)
    fallback = client.get("/api/v2/rankings/predict", params={"team_a": "1", "team_b": "2"}).json()
    assert fallback["match"]["team_a"] == pytest.approx(expected_a)
    assert fallback["match"]["team_b"] == pytest.approx(1.0 - expected_a)

    # The 90-day activity cutoff needs include_inactive; the 180-day window caps the minimum count.
    monkeypatch.setattr(team_rankings, "ranking_date", lambda: date(2026, 1, 15))
    assert client.get("/api/v2/rankings/").json()["total"] == 0
    assert client.get("/api/v2/rankings/", params={"include_inactive": True}).json()["total"] == 2
    monkeypatch.setattr(team_rankings, "ranking_date", lambda: date(2026, 4, 15))
    assert client.get("/api/v2/rankings/", params={"include_inactive": True}).json()["total"] == 0


def test_rankings_api_filters_and_ranks_by_region(monkeypatch, ranking_sessions):
    monkeypatch.setattr(team_rankings, "ranking_date", lambda: date(2025, 10, 2))

    async def seed():
        # Two teams per region, each winning five series, so a regional rank can
        # differ from the overall rank when both league leaders tie on Elo.
        leagues = [
            ("20", "Champions Tour 2025: Americas", Circuit.VCT, "1", "2"),
            ("21", "Challengers 2025: EMEA", Circuit.VCL, "3", "4"),
        ]
        async with ranking_sessions.begin() as session:
            session.add_all(
                EventRecord(id=event_id, name=title, circuit=circuit) for event_id, title, circuit, *_ in leagues
            )
            session.add_all(
                Team(id=team_id, source="test", first_seen_at=0, last_fetched_at=0) for team_id in ("1", "2", "3", "4")
            )
            await session.flush()
            for event_id, _, _, team_a_id, team_b_id in leagues:
                for index in range(5):
                    session.add(
                        MatchRecord(
                            id=f"{event_id}{index}",
                            event_id=event_id,
                            stage=None,
                            played_on=date(2025, 10, 1),
                            team_a_id=team_a_id,
                            team_b_id=team_b_id,
                            team_a_score=2,
                            team_b_score=0,
                            patch=None,
                            source="test",
                            ingested_at=0,
                        )
                    )
                    await session.flush()
                    session.add_all(
                        MapRecord(
                            match_id=f"{event_id}{index}",
                            map_index=map_index,
                            name="Map",
                            team_a_score=13,
                            team_b_score=7,
                        )
                        for map_index in range(2)
                    )
        async with ranking_sessions.begin() as session:
            await team_rankings.rebuild_ratings(session)

    asyncio.run(seed())

    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    client = TestClient(app)

    # The region filter narrows the ranking like the circuit filter: rank is the
    # position inside the region, while overall_rank stays global.
    all_teams = {"min_matches": 0, "include_inactive": True}
    americas = client.get("/api/v2/rankings/", params=all_teams | {"region": "americas"}).json()
    assert (americas["region"], americas["total"]) == ("americas", 2)
    assert [
        (row["team"]["id"], row["team"]["region"], row["rank"], row["overall_rank"], row["region"])
        for row in americas["teams"]
    ] == [
        ("1", "americas", 1, 1, "americas"),
        ("2", "americas", 2, 3, "americas"),
    ]
    emea = client.get("/api/v2/rankings/", params=all_teams | {"region": "EMEA"}).json()
    assert [(row["team"]["id"], row["rank"]) for row in emea["teams"]] == [("3", 1), ("4", 2)]
    assert client.get("/api/v2/rankings/", params=all_teams | {"region": "all"}).json()["total"] == 4
    assert client.get("/api/v2/rankings/", params=all_teams | {"region": "china"}).json()["total"] == 0

    # The circuit and region filters combine.
    combined = client.get("/api/v2/rankings/", params=all_teams | {"circuit": "vct", "region": "americas"}).json()
    assert [row["team"]["id"] for row in combined["teams"]] == ["1", "2"]
    assert client.get("/api/v2/rankings/", params=all_teams | {"circuit": "vct", "region": "emea"}).json()["total"] == 0

    # The profile carries the region on the team itself as well as at the top level,
    # with whole-number ratings.
    profile = client.get("/api/v2/rankings/teams/2").json()
    assert (profile["region"], profile["team"]["region"], profile["region_rank"], profile["rank"]) == (
        "americas",
        "americas",
        2,
        3,
    )
    assert (profile["elo"], profile["map_elo"]) == (1380, 1260)
    assert client.get("/api/v2/rankings/", params={"region": "nope"}).status_code == 422

    # The prediction summary carries the same region and rounded rating on its sides.
    prediction = client.get("/api/v2/rankings/predict", params={"team_a": "1", "team_b": "2"}).json()
    assert (prediction["team_a"]["region"], prediction["team_a"]["elo"], prediction["team_a"]["map_elo"]) == (
        "americas",
        1620,
        1740,
    )
    assert (prediction["team_b"]["region"], prediction["team_b"]["map_elo"]) == ("americas", 1260)


@pytest.mark.asyncio
async def test_external_match_probability_uses_a_unix_socket_when_configured(monkeypatch):
    # A unix:// URL must reach the service over its socket rather than as a TCP host.
    monkeypatch.setattr(settings, "PREDICTION_SERVICE_URL", "unix:///tmp/mock.sock")
    transport = MagicMock(return_value=AsyncMock())
    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", transport)
    post = AsyncMock(
        return_value=MagicMock(
            json=lambda: {
                "team_probabilities": [
                    {"team_id": "1", "probability": 0.75},
                    {"team_id": "2", "probability": 0.25},
                ]
            }
        )
    )
    monkeypatch.setattr(httpx2.AsyncClient, "post", post)

    probability = await team_rankings._external_match_probability("1", "2", date(2025, 10, 2))

    assert probability == 0.75
    transport.assert_called_once_with(uds="/tmp/mock.sock")
    assert post.call_args.args == ("http://localhost/predict",)
