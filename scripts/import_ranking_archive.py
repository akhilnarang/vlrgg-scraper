"""Seed the Elo ledger with historical matches from a match archive, then rebuild the ratings.

The team rankings cron only ingests matches still on VLR's results listing, so a new
ledger holds a few weeks of series: too few for any team to reach the ranking minimum
and too few tiered events to classify a VCT team's region. This imports the
``history.sqlite`` bundle built by valorant-prediction-model from the match archive
(its ``matches``, ``maps``, and ``identities`` tables).

A series is imported when its decisive maps agree with the recorded winner; forfeits
without played maps are skipped. Matches, teams, and events already stored are left
as they are, so a rerun only adds what is missing and never overwrites live-ingested
data. Event circuits come from VLR's tier listings, crawled before the import
transaction opens. Run where the app runs, after startup has applied migrations.

Usage (from the repo root):
    uv run python -m scripts.import_ranking_archive path/to/history.sqlite
"""

import argparse
import asyncio
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.constants import Circuit
from app.core.config import settings
from app.db.engine import create_engine
from app.db.models import EventRecord, MapRecord, MatchRecord, Team
from app.services import ranking_store, scrape_store, team_rankings
from app.services.events import tier_event_circuits


@dataclass(slots=True, frozen=True)
class ArchivedMatch:
    """One archived series with the maps that decided it."""

    id: str
    event_id: str
    event_name: str
    played_on: date
    patch: str | None
    team_a_id: str
    team_b_id: str
    team_a_score: int
    team_b_score: int
    maps: list[tuple[int, str, int, int]]


def read_archive(path: Path) -> tuple[list[ArchivedMatch], dict[str, str]]:
    """Read the ratable series and the latest team names from an archive bundle.

    :param path: Path to the ``history.sqlite`` bundle.
    :return: Series in archive order, and team names keyed by team ID.
    """
    archive = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names: dict[tuple[str, str], str] = {}
        for kind, entity_id, name in archive.execute("SELECT kind, entity_id, name FROM identities ORDER BY last_seen"):
            names[(kind, entity_id)] = name
        maps: dict[str, list[tuple[int, str, int, int]]] = {}
        for match_id, index, name, score_a, score_b in archive.execute(
            "SELECT match_id, map_index, map_name, score_a, score_b FROM maps"
            " WHERE score_a IS NOT NULL AND score_b IS NOT NULL AND score_a != score_b"
            " ORDER BY match_id, map_index"
        ):
            maps.setdefault(match_id, []).append((index, name or "Unknown", score_a, score_b))
        matches = []
        for match_id, played_on, event_id, event_name, patch, team_a, team_b, winner in archive.execute(
            "SELECT match_id, played_on, event_id, event_name, patch, team_a_id, team_b_id, winner_team_id"
            " FROM matches WHERE event_id IS NOT NULL AND team_a_id IS NOT NULL AND team_b_id IS NOT NULL"
            " ORDER BY played_on, match_id"
        ):
            played = maps.get(match_id, [])
            team_a_score = sum(score_a > score_b for _, _, score_a, score_b in played)
            team_b_score = len(played) - team_a_score
            if team_a_score == team_b_score or winner != (team_a if team_a_score > team_b_score else team_b):
                continue
            matches.append(
                ArchivedMatch(
                    id=match_id,
                    event_id=event_id,
                    event_name=event_name or names.get(("event", event_id), event_id),
                    played_on=date.fromisoformat(played_on),
                    patch=patch,
                    team_a_id=team_a,
                    team_b_id=team_b,
                    team_a_score=team_a_score,
                    team_b_score=team_b_score,
                    maps=played,
                )
            )
    finally:
        archive.close()
    return matches, {entity_id: name for (kind, entity_id), name in names.items() if kind == "team"}


async def main(path: Path, tier_pages: int) -> None:
    """Insert the archived series missing from the ledger and rebuild the ratings in one transaction.

    :param path: Path to the ``history.sqlite`` bundle.
    :param tier_pages: Highest VLR tier listing page crawled per tier.
    :return: None.
    """
    archived, team_names = read_archive(path)
    engine = create_engine(settings.DATABASE_URL)
    sessions = async_sessionmaker(engine)
    try:
        async with sessions() as session:
            stored_matches = set(await session.scalars(select(MatchRecord.id)))
            stored_events = set(await session.scalars(select(EventRecord.id)))
            stored_teams = set(await session.scalars(select(Team.id)))
        missing = [match for match in archived if match.id not in stored_matches]
        events = {match.event_id: match.event_name for match in missing if match.event_id not in stored_events}
        teams = {team for match in missing for team in (match.team_a_id, match.team_b_id)} - stored_teams
        circuits = await tier_event_circuits(max_pages=tier_pages) if events else {}
        now = int(time.time())
        async with sessions.begin() as session:
            for team_id in sorted(teams):
                await scrape_store.upsert_team(session, team_id, name=team_names.get(team_id))
            for event_id, name in events.items():
                await ranking_store.set_event_circuit(
                    session, event_id, name, circuits.get(event_id, Circuit.OTHER), now
                )
            if missing:
                await session.execute(
                    insert(MatchRecord),
                    [
                        {
                            "id": match.id,
                            "event_id": match.event_id,
                            "played_on": match.played_on,
                            "team_a_id": match.team_a_id,
                            "team_b_id": match.team_b_id,
                            "team_a_score": match.team_a_score,
                            "team_b_score": match.team_b_score,
                            "patch": match.patch,
                            "source": "archive",
                            "ingested_at": now,
                        }
                        for match in missing
                    ],
                )
                await session.execute(
                    insert(MapRecord),
                    [
                        {"match_id": match.id, "map_index": index, "name": name, "team_a_score": a, "team_b_score": b}
                        for match in missing
                        for index, name, a, b in match.maps
                    ],
                )
            await team_rankings.rebuild_ratings(session)
        print(
            f"archive: {len(archived)} ratable series; imported {len(missing)} matches, "
            f"{len(teams)} teams, {len(events)} events ({sum(e in circuits for e in events)} with a tier circuit)"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive", type=Path, help="history.sqlite bundle built by valorant-prediction-model")
    parser.add_argument("--tier-pages", type=int, default=10, help="VLR tier listing pages crawled per tier")
    args = parser.parse_args()
    asyncio.run(main(args.archive, args.tier_pages))
