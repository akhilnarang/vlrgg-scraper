"""Database queries for the Elo team rankings. Never commits; the caller owns the transaction."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import Circuit, RankingScope
from app.db.models import EventRecord, MapRecord, MatchRecord, RankingResult, Team, TeamCircuit, TeamElo


@dataclass(slots=True, frozen=True)
class RankedElo:
    """A team with at least one rated series and its stored ratings."""

    team: Team
    elo: TeamElo


@dataclass(slots=True, frozen=True)
class RecentResult:
    """One series ledger row with its match and event."""

    result: RankingResult
    match: MatchRecord
    event: EventRecord


@dataclass(slots=True, frozen=True)
class StoredMap:
    """One decisive stored map, oriented to its match's team order."""

    map_index: int
    team_a_won: bool


@dataclass(slots=True, frozen=True)
class StoredMatch:
    """A ratable stored match with its event, circuit, and decisive maps."""

    match_id: str
    event_id: str
    event_name: str
    played_on: date
    circuit: Circuit
    team_a_id: str
    team_b_id: str
    team_a_score: int
    team_b_score: int
    maps: tuple[StoredMap, ...]


@dataclass(slots=True, frozen=True)
class StoredListing:
    """What the ledger holds for one listed match, to detect a needed repair."""

    played_on: date
    ingested_at: int
    team_a_id: str | None
    team_b_id: str | None
    team_a_score: int | None
    team_b_score: int | None
    maps: int

    @property
    def complete(self) -> bool:
        """Whether the stored match has everything the ratings need.

        A decisive series has exactly ``team_a_score + team_b_score`` played
        maps, so a partial fetch that stored fewer of them (or extra obsolete
        ones) cannot compute a map share and still needs repair.

        :return: True when both teams, a decisive score, and every played map are stored.
        """
        return (
            self.team_a_id is not None
            and self.team_b_id is not None
            and self.team_a_score is not None
            and self.team_b_score is not None
            and self.team_a_score != self.team_b_score
            and self.maps == self.team_a_score + self.team_b_score
        )


async def stored_listings(session: AsyncSession) -> dict[str, StoredListing]:
    """Read what the ledger holds for each match, keyed by match ID.

    :param session: Caller-owned database session.
    :return: Stored summaries the cron compares with the VLR listing.
    """
    map_counts = select(MapRecord.match_id, func.count().label("maps")).group_by(MapRecord.match_id).subquery()
    rows = await session.execute(
        select(MatchRecord, func.coalesce(map_counts.c.maps, 0)).outerjoin(
            map_counts, map_counts.c.match_id == MatchRecord.id
        )
    )
    return {
        match.id: StoredListing(
            played_on=match.played_on,
            ingested_at=match.ingested_at,
            team_a_id=match.team_a_id,
            team_b_id=match.team_b_id,
            team_a_score=match.team_a_score,
            team_b_score=match.team_b_score,
            maps=maps,
        )
        for match, maps in rows
    }


async def stored_matches(session: AsyncSession) -> list[StoredMatch]:
    """Read every ratable stored match with its circuit and decisive maps, oldest first.

    A match is ratable when both teams and a decisive score are stored. Maps with
    equal scores carry no winner and are left out of the map ratings.

    :param session: Caller-owned database session.
    :return: Matches ordered by ``(played_on, id)``.
    """
    rows = await session.execute(
        select(MatchRecord, EventRecord.circuit, EventRecord.name)
        .join(EventRecord, EventRecord.id == MatchRecord.event_id)
        .where(MatchRecord.team_a_id.is_not(None))
        .where(MatchRecord.team_b_id.is_not(None))
        .where(MatchRecord.team_a_score.is_not(None))
        .where(MatchRecord.team_b_score.is_not(None))
        .where(MatchRecord.team_a_score != MatchRecord.team_b_score)
        .order_by(MatchRecord.played_on, MatchRecord.id)
    )
    maps: dict[str, list[StoredMap]] = defaultdict(list)
    map_rows = await session.execute(
        select(MapRecord)
        .where(MapRecord.team_a_score != MapRecord.team_b_score)
        .order_by(MapRecord.match_id, MapRecord.map_index)
    )
    for row in map_rows.scalars():
        maps[row.match_id].append(StoredMap(map_index=row.map_index, team_a_won=row.team_a_score > row.team_b_score))
    return [
        StoredMatch(
            match_id=match.id,
            event_id=match.event_id,
            event_name=event_name,
            played_on=match.played_on,
            circuit=circuit,
            team_a_id=cast(str, match.team_a_id),
            team_b_id=cast(str, match.team_b_id),
            team_a_score=cast(int, match.team_a_score),
            team_b_score=cast(int, match.team_b_score),
            maps=tuple(maps.get(match.id, ())),
        )
        for match, circuit, event_name in rows
    ]


async def needs_rebuild(session: AsyncSession) -> bool:
    """Whether the stored ledger no longer matches the ratable stored matches.

    The cron commits each match before rebuilding, so a run that died in
    between leaves committed matches and maps without their ledger rows.
    Comparing the two counts recovers that rebuild on the next run even when
    the run itself changed nothing. A correction that keeps the counts equal,
    such as a flipped winner, a score change, or a swapped opponent, is invisible
    to the counts, so every ledger outcome is also compared with the winner and
    participants stored for its match or map.

    :param session: Caller-owned database session.
    :return: True when a replay would change the stored ledger.
    """
    ratable = (
        MatchRecord.team_a_id.is_not(None),
        MatchRecord.team_b_id.is_not(None),
        MatchRecord.team_a_score.is_not(None),
        MatchRecord.team_b_score.is_not(None),
        MatchRecord.team_a_score != MatchRecord.team_b_score,
    )
    stored_matches = await session.scalar(select(func.count()).select_from(MatchRecord).where(*ratable))
    ledger_matches = await session.scalar(
        select(func.count()).select_from(RankingResult).where(RankingResult.scope == RankingScope.MATCH)
    )
    stored_maps = await session.scalar(
        select(func.count())
        .select_from(MapRecord)
        .join(MatchRecord, MatchRecord.id == MapRecord.match_id)
        .where(*ratable)
        .where(MapRecord.team_a_score != MapRecord.team_b_score)
    )
    ledger_maps = await session.scalar(
        select(func.count()).select_from(RankingResult).where(RankingResult.scope == RankingScope.MAP)
    )
    if (stored_matches, stored_maps) != (ledger_matches, ledger_maps):
        return True
    match_winner = case(
        (MatchRecord.team_a_score > MatchRecord.team_b_score, MatchRecord.team_a_id),
        else_=MatchRecord.team_b_id,
    )
    match_corrections = await session.scalar(
        select(func.count())
        .select_from(RankingResult)
        .join(MatchRecord, MatchRecord.id == RankingResult.match_id)
        .where(*ratable)
        .where(RankingResult.scope == RankingScope.MATCH)
        .where(
            or_(
                RankingResult.winner_team_id != match_winner,
                RankingResult.team_a_id != MatchRecord.team_a_id,
                RankingResult.team_b_id != MatchRecord.team_b_id,
                RankingResult.played_on != MatchRecord.played_on,
            )
        )
    )
    map_winner = case(
        (MapRecord.team_a_score > MapRecord.team_b_score, MatchRecord.team_a_id),
        else_=MatchRecord.team_b_id,
    )
    map_corrections = await session.scalar(
        select(func.count())
        .select_from(RankingResult)
        .join(
            MapRecord,
            and_(MapRecord.match_id == RankingResult.match_id, MapRecord.map_index == RankingResult.map_index),
        )
        .join(MatchRecord, MatchRecord.id == MapRecord.match_id)
        .where(*ratable)
        .where(MapRecord.team_a_score != MapRecord.team_b_score)
        .where(RankingResult.scope == RankingScope.MAP)
        .where(
            or_(
                RankingResult.winner_team_id != map_winner,
                RankingResult.team_a_id != MatchRecord.team_a_id,
                RankingResult.team_b_id != MatchRecord.team_b_id,
                RankingResult.played_on != MatchRecord.played_on,
            )
        )
    )
    return bool(match_corrections or map_corrections)


async def series_window_counts(session: AsyncSession, start: date, end: date) -> dict[str, int]:
    """Count each team's usable series inside a date window.

    A usable series has both teams and a decisive score, the same matches a
    ratings rebuild rates.

    :param session: Caller-owned database session.
    :param start: First date in the window.
    :param end: Last date in the window.
    :return: Counts keyed by team ID; teams without a series in the window are absent.
    """
    rows = await session.execute(
        select(MatchRecord.team_a_id, MatchRecord.team_b_id)
        .where(MatchRecord.played_on.between(start, end))
        .where(MatchRecord.team_a_id.is_not(None))
        .where(MatchRecord.team_b_id.is_not(None))
        .where(MatchRecord.team_a_score.is_not(None))
        .where(MatchRecord.team_b_score.is_not(None))
        .where(MatchRecord.team_a_score != MatchRecord.team_b_score)
    )
    return dict(Counter(cast(str, team_id) for pair in rows for team_id in pair))


async def latest_patch(session: AsyncSession) -> str | None:
    """Read the patch of the most recently played match that carries one.

    :param session: Caller-owned database session.
    :return: Newest match's patch, or None when no stored match has one.
    """
    return await session.scalar(
        select(MatchRecord.patch)
        .where(MatchRecord.patch.is_not(None))
        .order_by(MatchRecord.played_on.desc(), MatchRecord.id.desc())
        .limit(1)
    )


async def replace_rankings(
    session: AsyncSession,
    results: Sequence[Mapping[str, object]],
    elos: Sequence[Mapping[str, object]],
    circuits: Sequence[Mapping[str, object]],
) -> None:
    """Replace the ledger, team ratings, and circuit counts with a replayed set.

    :param session: Caller-owned database session.
    :param results: Ledger rows to store, in replay order.
    :param elos: Team Elo rows to store.
    :param circuits: Team circuit rows to store.
    :return: None.
    """
    await session.execute(delete(RankingResult))
    await session.execute(delete(TeamElo))
    await session.execute(delete(TeamCircuit))
    if results:
        await session.execute(insert(RankingResult), list(results))
    if elos:
        await session.execute(insert(TeamElo), list(elos))
    if circuits:
        await session.execute(insert(TeamCircuit), list(circuits))


async def upsert_event(session: AsyncSession, event_id: str, name: str, circuit: Circuit) -> None:
    """Store an event's name and circuit, leaving its circuit-check timestamp alone.

    :param session: Caller-owned database session.
    :param event_id: Event ID.
    :param name: Event title.
    :param circuit: Circuit the event's matches count towards.
    :return: None.
    """
    statement = insert(EventRecord).values(id=event_id, name=name, circuit=circuit)
    await session.execute(
        statement.on_conflict_do_update(index_elements=[EventRecord.id], set_={"name": name, "circuit": circuit})
    )


async def set_event_circuit(session: AsyncSession, event_id: str, name: str, circuit: Circuit, checked_at: int) -> None:
    """Store an event's resolved circuit and when the tier listings were read.

    :param session: Caller-owned database session.
    :param event_id: Event ID.
    :param name: Event title.
    :param circuit: Resolved circuit.
    :param checked_at: Unix time the circuit was resolved.
    :return: None.
    """
    statement = insert(EventRecord).values(id=event_id, name=name, circuit=circuit, circuit_checked_at=checked_at)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[EventRecord.id],
            set_={"name": name, "circuit": circuit, "circuit_checked_at": checked_at},
        )
    )


async def stale_other_events(session: AsyncSession, checked_before: int) -> list[EventRecord]:
    """Read events still assigned no circuit whose tier listing check has gone stale.

    :param session: Caller-owned database session.
    :param checked_before: Unix time before which a circuit check counts as stale.
    :return: Events to look up in the tier listings again, ordered by ID.
    """
    rows = await session.execute(
        select(EventRecord)
        .where(EventRecord.circuit == Circuit.OTHER)
        .where(EventRecord.circuit_checked_at < checked_before)
        .order_by(EventRecord.id)
    )
    return list(rows.scalars())


async def teams_by_id(session: AsyncSession, team_ids: Iterable[str]) -> dict[str, Team]:
    """Read stored teams by ID.

    :param session: Caller-owned database session.
    :param team_ids: Team IDs.
    :return: Team rows keyed by ID; unknown IDs are absent.
    """
    rows = await session.execute(select(Team).where(Team.id.in_(list(team_ids))))
    return {row.id: row for row in rows.scalars()}


async def elos(session: AsyncSession, team_ids: Iterable[str]) -> dict[str, TeamElo]:
    """Read the stored Elo of the given teams.

    :param session: Caller-owned database session.
    :param team_ids: Team IDs.
    :return: Elo rows keyed by team ID; unknown teams are absent.
    """
    rows = await session.execute(select(TeamElo).where(TeamElo.team_id.in_(list(team_ids))))
    return {row.team_id: row for row in rows.scalars()}


async def ranked_elos(session: AsyncSession) -> list[RankedElo]:
    """Read every team with at least one rated series, with its stored ratings.

    :param session: Caller-owned database session.
    :return: Team and Elo rows in no particular order.
    """
    rows = await session.execute(
        select(Team, TeamElo).join(TeamElo, TeamElo.team_id == Team.id).where(TeamElo.matches > 0)
    )
    return [RankedElo(*row) for row in rows.all()]


async def team_circuits(session: AsyncSession, active_since: date | None = None) -> dict[str, list[TeamCircuit]]:
    """Read circuit activity grouped by team.

    :param session: Caller-owned database session.
    :param active_since: Only circuits last played on or after this date; None reads the full history.
    :return: Circuit rows keyed by team ID, most played first.
    """
    query = select(TeamCircuit)
    if active_since is not None:
        query = query.where(TeamCircuit.last_played_on >= active_since)
    rows = await session.execute(query.order_by(TeamCircuit.matches.desc(), TeamCircuit.circuit))
    grouped: dict[str, list[TeamCircuit]] = {}
    for row in rows.scalars():
        grouped.setdefault(row.team_id, []).append(row)
    return grouped


async def recent_results(session: AsyncSession, team_id: str, limit: int) -> list[RecentResult]:
    """Read a team's most recent series results with their event.

    :param session: Caller-owned database session.
    :param team_id: Team ID.
    :param limit: Maximum results to return.
    :return: Series ledger rows, newest first.
    """
    query = (
        select(RankingResult, MatchRecord, EventRecord)
        .join(MatchRecord, MatchRecord.id == RankingResult.match_id)
        .join(EventRecord, EventRecord.id == MatchRecord.event_id)
        .where(RankingResult.scope == RankingScope.MATCH)
        .where(or_(RankingResult.team_a_id == team_id, RankingResult.team_b_id == team_id))
        .order_by(RankingResult.played_on.desc(), RankingResult.seq.desc())
        .limit(limit)
    )
    return [RecentResult(*row) for row in (await session.execute(query)).all()]


async def head_to_head_results(
    session: AsyncSession, team_a_id: str, team_b_id: str
) -> tuple[list[RankingResult], list[RankingResult]]:
    """Read every series and map between two teams, in either orientation.

    :param session: Caller-owned database session.
    :param team_a_id: First team's ID.
    :param team_b_id: Second team's ID.
    :return: Series results and the map results of those series.
    """
    pair = or_(
        and_(RankingResult.team_a_id == team_a_id, RankingResult.team_b_id == team_b_id),
        and_(RankingResult.team_a_id == team_b_id, RankingResult.team_b_id == team_a_id),
    )
    match_rows = list(
        (
            await session.execute(
                select(RankingResult)
                .where(RankingResult.scope == RankingScope.MATCH)
                .where(pair)
                .order_by(RankingResult.played_on)
            )
        ).scalars()
    )
    if not match_rows:
        return [], []
    map_rows = list(
        (
            await session.execute(
                select(RankingResult)
                .where(RankingResult.scope == RankingScope.MAP)
                .where(RankingResult.match_id.in_([row.match_id for row in match_rows]))
            )
        ).scalars()
    )
    return match_rows, map_rows
