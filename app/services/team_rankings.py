"""Elo team rankings: ratings, ranked lists, profiles, predictions, and ingestion."""

import logging
import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import groupby
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.constants import (
    ELO_ALGORITHM,
    ELO_BASE,
    ELO_K,
    ELO_MAP_ALPHA,
    MATCH_SCOPE_MAP_INDEX,
    MATCH_TIMEZONE,
    RANKING_ACTIVE_DAYS,
    RANKING_MIN_MATCHES,
    RANKING_WINDOW_DAYS,
    Circuit,
    PredictionFallbackReason,
    PredictionRating,
    RankingOrder,
    RankingScope,
    RankingSort,
    Region,
)
from app.db.models import EventRecord, MapRecord, MatchRecord, Team, TeamCircuit, TeamElo
from app.exceptions import NotFoundError
from app.schemas.matches import TeamWithImage
from app.schemas.predictions import EloFallbackWarning, EloPredictionSource
from app.services import predictions, ranking_store, scrape_store
from app.services.events import tier_event_circuits

logger = logging.getLogger(__name__)

_MATCH_TZ = ZoneInfo(MATCH_TIMEZONE)

REPAIR_COOLDOWN_SECONDS = 4 * 3600
REPAIR_GIVE_UP_DAYS = 7

_REGION_EVENT_WINDOW = 10
_REGION_CIRCUITS = frozenset({Circuit.VCT, Circuit.VCL, Circuit.GC})
_REGION_TITLE_PATTERNS = {
    Region.AMERICAS: re.compile(
        r"americas|north america|\bna\b|brazil|brasil|latam|latin america|gamers club",
        re.IGNORECASE,
    ),
    Region.EMEA: re.compile(
        r"emea|europe|dach|france|spain|t[üu]rkiye|turkey|north//east|nordic|italy|polska|poland"
        r"|portugal|iberia|\buk\b|east surge|mena|middle east|north africa|arabia|polaris",
        re.IGNORECASE,
    ),
    Region.PACIFIC: re.compile(
        r"pacific|korea|japan|oceania|australia|southeast asia|\bsea\b|philippines|indonesia|vietnam"
        r"|thailand|malaysia|singapore|south asia|\bindia\b|apac|asia-pacific|taiwan|hong kong|tw/hk",
        re.IGNORECASE,
    ),
    Region.CHINA: re.compile(r"china", re.IGNORECASE),
}


def event_region(name: str) -> Region | None:
    """Return the competitive region an event title declares.

    VCT, VCL, and Game Changers events name their league or sub-region in the
    title; anything else, such as an offseason event, declares no region.

    :param name: Event title.
    :return: Declared region, or None when the title names none.
    """
    for region, pattern in _REGION_TITLE_PATTERNS.items():
        if pattern.search(name):
            return region
    return None


@dataclass(slots=True, frozen=True, order=True)
class _RegionalEvent:
    """One tiered event a team played, with the region its title declares."""

    played_on: date
    event_id: str
    region: Region = field(compare=False)


def _team_region(events: Iterable[_RegionalEvent]) -> Region | None:
    """Choose the region a team represents from its classified tiered events.

    Only the most recent distinct events vote. The most frequent region wins;
    the most recently played wins a tie.

    :param events: Tiered events the team played, with their declared region.
    :return: The team's region, or None when no event declares one.
    """
    recent = sorted(events)[-_REGION_EVENT_WINDOW:]
    ranked = Counter(e.region for e in reversed(recent)).most_common(1)
    return ranked[0][0] if ranked else None


def ranking_date() -> date:
    """Return today in the timezone matches are dated in, the date rankings are read on.

    :return: Current match-timezone date.
    """
    return datetime.now(_MATCH_TZ).date()


def window_start(as_of: date) -> date:
    """Return the first date whose series count towards the ranking minimum.

    :param as_of: Ranking date.
    :return: First date of the inclusive 180-day window.
    """
    return as_of - timedelta(days=RANKING_WINDOW_DAYS - 1)


def expected(elo_a: float, elo_b: float) -> float:
    """Return the Elo win probability of the first side.

    The rating gap is clamped so an extreme mismatch cannot overflow the
    exponent and produce an exact 0 or 1.

    :param elo_a: First side's rating.
    :param elo_b: Second side's rating.
    :return: Probability the first side wins, in (0, 1).
    """
    exponent = max(-20.0, min(20.0, (elo_b - elo_a) / 400.0))
    return 1.0 / (1.0 + 10.0**exponent)


@dataclass(slots=True, frozen=True)
class DayUpdate:
    """One result's rating change within a calendar day."""

    team_a_before: float
    team_b_before: float
    delta: float


def apply_day(ratings: dict[str, float], results: list[tuple[str, str, float]]) -> list[DayUpdate]:
    """Score one calendar day against the ratings as they were before the day.

    Every result on the day is rated against the same pre-day ratings, so a
    team playing twice on a day meets both opponents with the rating it had
    that morning. Each team's changes are then summed in one step with
    ``math.fsum``.

    :param ratings: Rating by team ID, updated in place.
    :param results: ``(team_a_id, team_b_id, observed_a)`` results in match order.
    :return: One update per result, in the same order.
    """
    before = {
        team_id: ratings.get(team_id, ELO_BASE)
        for team_a_id, team_b_id, _ in results
        for team_id in (team_a_id, team_b_id)
    }
    changes: dict[str, list[float]] = defaultdict(list)
    updates = []
    for team_a_id, team_b_id, observed_a in results:
        delta = ELO_K * (observed_a - expected(before[team_a_id], before[team_b_id]))
        changes[team_a_id].append(delta)
        changes[team_b_id].append(-delta)
        updates.append(DayUpdate(before[team_a_id], before[team_b_id], delta))
    for team_id, team_changes in changes.items():
        ratings[team_id] = before[team_id] + math.fsum(team_changes)
    return updates


def _map_share(match: ranking_store.StoredMatch) -> float | None:
    """Return team A's map-win share when the stored maps match the series score.

    :param match: Stored match with its decisive maps.
    :return: Share in (0, 1), or None when the stored maps are incomplete.
    """
    wins_a = sum(item.team_a_won for item in match.maps)
    wins_b = len(match.maps) - wins_a
    if wins_a != match.team_a_score or wins_b != match.team_b_score:
        return None
    return match.team_a_score / (match.team_a_score + match.team_b_score)


def _observed(match: ranking_store.StoredMatch) -> float:
    """Return the result a series rating moves by, blending in the map share.

    :param match: Stored match with its decisive maps.
    :return: Observed result for team A, with ``ELO_MAP_ALPHA`` on a valid map share.
    """
    outcome = 1.0 if match.team_a_score > match.team_b_score else 0.0
    share = _map_share(match)
    if share is None:
        return outcome
    return (1.0 - ELO_MAP_ALPHA) * outcome + ELO_MAP_ALPHA * share


@dataclass(slots=True)
class RatingState:
    """One team's running record while a replay updates its rating."""

    matches: int = 0
    match_wins: int = 0
    maps: int = 0
    map_wins: int = 0
    first_played_on: date | None = None
    last_played_on: date | None = None


@dataclass(slots=True, frozen=True)
class ReplayResult:
    """Everything a full ratings replay produces, ready to replace the stored rows."""

    results: list[dict[str, object]]
    elos: list[dict[str, object]]
    circuits: list[dict[str, object]]


def _record(state: RatingState, played_on: date, *, series: bool, won: bool) -> None:
    """Add one rated result to a team's record.

    :param state: Team state, updated in place.
    :param played_on: Date the result was played.
    :param series: Whether the result is a series rather than a map.
    :param won: Whether the team won the result.
    :return: None.
    """
    if series:
        state.matches += 1
        state.match_wins += int(won)
    else:
        state.maps += 1
        state.map_wins += int(won)
    if state.first_played_on is None:
        state.first_played_on = played_on
    state.last_played_on = played_on


def _ledger_row(
    scope: RankingScope,
    match: ranking_store.StoredMatch,
    map_index: int,
    team_a_won: bool,
    update: DayUpdate,
) -> dict[str, object]:
    """Build the ledger row for one applied result.

    :param scope: Series or map.
    :param match: Stored match the result belongs to.
    :param map_index: Map position, or ``MATCH_SCOPE_MAP_INDEX`` for a series.
    :param team_a_won: Whether the first side won the result.
    :param update: Rating change the result produced.
    :return: Ledger row.
    """
    return {
        "match_id": match.match_id,
        "scope": scope,
        "map_index": map_index,
        "played_on": match.played_on,
        "team_a_id": match.team_a_id,
        "team_b_id": match.team_b_id,
        "winner_team_id": match.team_a_id if team_a_won else match.team_b_id,
        "team_a_elo_before": update.team_a_before,
        "team_b_elo_before": update.team_b_before,
        "team_a_delta": update.delta,
    }


def replay_ratings(matches: list[ranking_store.StoredMatch]) -> ReplayResult:
    """Replay stored matches into ratings, ledger rows, circuit, and region activity.

    The replay is a pure function of the stored matches: they are sorted by
    ``(played_on, id)`` and rated a calendar day at a time, so how a match
    arrived or in which order a run fetched it can never move a rating.

    :param matches: Ratable stored matches.
    :return: Ledger rows, team Elo rows, and team circuit rows.
    """
    ordered = sorted(matches, key=lambda match: (match.played_on, match.match_id))
    match_ratings: dict[str, float] = {}
    map_ratings: dict[str, float] = {}
    states: dict[str, RatingState] = defaultdict(RatingState)
    results: list[dict[str, object]] = []
    circuits: dict[tuple[str, Circuit], tuple[int, date]] = {}
    regions: dict[str, dict[str, _RegionalEvent]] = defaultdict(dict)
    for played_on, day in groupby(ordered, key=lambda match: match.played_on):
        day_matches = list(day)
        series_updates = apply_day(
            match_ratings,
            [(match.team_a_id, match.team_b_id, _observed(match)) for match in day_matches],
        )
        map_updates = iter(
            apply_day(
                map_ratings,
                [
                    (match.team_a_id, match.team_b_id, 1.0 if played.team_a_won else 0.0)
                    for match in day_matches
                    for played in match.maps
                ],
            )
        )
        for match, update in zip(day_matches, series_updates, strict=True):
            state_a = states[match.team_a_id]
            state_b = states[match.team_b_id]
            team_a_won = match.team_a_score > match.team_b_score
            _record(state_a, played_on, series=True, won=team_a_won)
            _record(state_b, played_on, series=True, won=not team_a_won)
            results.append(_ledger_row(RankingScope.MATCH, match, MATCH_SCOPE_MAP_INDEX, team_a_won, update))
            for played in match.maps:
                _record(state_a, played_on, series=False, won=played.team_a_won)
                _record(state_b, played_on, series=False, won=not played.team_a_won)
                results.append(
                    _ledger_row(RankingScope.MAP, match, played.map_index, played.team_a_won, next(map_updates))
                )
            region = event_region(match.event_name) if match.circuit in _REGION_CIRCUITS else None
            for team_id in (match.team_a_id, match.team_b_id):
                count, last = circuits.get((team_id, match.circuit), (0, played_on))
                circuits[team_id, match.circuit] = (count + 1, max(last, played_on))
                if region is not None:
                    regions[team_id][match.event_id] = _RegionalEvent(played_on, match.event_id, region)
    return ReplayResult(
        results=results,
        elos=[
            {
                "team_id": team_id,
                "match_elo": match_ratings.get(team_id, ELO_BASE),
                "map_elo": map_ratings.get(team_id, ELO_BASE),
                "matches": state.matches,
                "match_wins": state.match_wins,
                "maps": state.maps,
                "map_wins": state.map_wins,
                "first_played_on": state.first_played_on,
                "last_played_on": state.last_played_on,
                "region": _team_region(regions.get(team_id, {}).values()),
            }
            for team_id, state in states.items()
        ],
        circuits=[
            {"team_id": team_id, "circuit": circuit, "matches": count, "last_played_on": last}
            for (team_id, circuit), (count, last) in circuits.items()
        ],
    )


async def rebuild_ratings(session: AsyncSession) -> None:
    """Rebuild every rating, ledger row, and circuit count from the stored matches.

    Ratings are a pure function of the stored matches and maps, so the order
    matches were fetched or stored in never matters: the cron upserts what it
    fetched and then replays the whole ledger.

    :param session: Caller-owned database session.
    :return: None.
    """
    replay = replay_ratings(await ranking_store.stored_matches(session))
    await ranking_store.replace_rankings(session, replay.results, replay.elos, replay.circuits)


def _stats(played: int, wins: int) -> schemas.Stats:
    """Build a record for one scope.

    :param played: Games played.
    :param wins: Games won.
    :return: Win-loss record.
    """
    return schemas.Stats(
        played=played,
        wins=wins,
        losses=played - wins,
        win_rate=wins / played if played else 0.0,
    )


def _team_summary(team: Team, region: Region | str | None = None) -> schemas.TeamSummary:
    """Build the shared team identity payload.

    The stored team row usually carries no region; the Elo row's classification
    fills that gap so every client reads the region off the team itself.

    :param team: Stored team row.
    :param region: Region from the team's Elo row, used when the stored row has none.
    :return: Team summary.
    """
    stored_region = team.region if team.region is not None else region
    return schemas.TeamSummary(
        id=team.id,
        name=team.name or "",
        tag=team.tag,
        logo=team.logo,
        country=team.country,
        region=stored_region.value if isinstance(stored_region, Region) else stored_region,
    )


def _new_team_elo(team_id: str) -> TeamElo:
    """Build the baseline rating of a team with no rated results.

    :param team_id: Team ID.
    :return: Unpersisted Elo row.
    """
    return TeamElo(
        team_id=team_id,
        match_elo=ELO_BASE,
        map_elo=ELO_BASE,
        matches=0,
        match_wins=0,
        maps=0,
        map_wins=0,
        first_played_on=None,
        last_played_on=None,
    )


def _sort_key(row: ranking_store.RankedElo, sort: RankingSort) -> float:
    """Return the value a sort mode ranks by, where higher is always better.

    :param row: Stored rating row.
    :param sort: Requested sort mode.
    :return: Comparable sort value.
    """
    if sort == RankingSort.MAP_ELO:
        return row.elo.map_elo
    if sort == RankingSort.MATCHES:
        return float(row.elo.matches)
    if sort == RankingSort.WIN_RATE:
        return row.elo.match_wins / row.elo.matches if row.elo.matches else 0.0
    return row.elo.match_elo


def _competition_ranks(rows: list[ranking_store.RankedElo], sort: RankingSort) -> dict[str, int]:
    """Assign competition ranks to rows already in the requested sort order.

    Ties share the position of the first tied row, so equal ratings never
    produce an arbitrary order.

    :param rows: Stored rating rows in ranking order.
    :param sort: Sort mode.
    :return: Competition rank keyed by team ID.
    """
    ranks: dict[str, int] = {}
    previous: float | None = None
    previous_rank = 0
    for index, row in enumerate(rows, start=1):
        value = _sort_key(row, sort)
        if previous is None or value != previous:
            previous_rank = index
        ranks[row.elo.team_id] = previous_rank
        previous = value
    return ranks


def _primary_circuit(rows: list[TeamCircuit]) -> Circuit | None:
    """Return the circuit a team played most, breaking ties by recency.

    :param rows: Team's circuit activity.
    :return: Primary circuit, or None when the team has none.
    """
    if not rows:
        return None
    return max(rows, key=lambda row: (row.matches, row.last_played_on)).circuit


def _is_active(elo: TeamElo, as_of: date) -> bool:
    """Whether a team played within the activity window.

    :param elo: Stored record.
    :param as_of: Ranking date.
    :return: True when the team's last match is recent enough.
    """
    return elo.last_played_on is not None and elo.last_played_on >= as_of - timedelta(days=RANKING_ACTIVE_DAYS)


def _filter_rows(
    rows: list[ranking_store.RankedElo],
    circuit_sets: dict[str, set[Circuit]],
    circuit: Circuit | None = None,
    region: Region | None = None,
) -> list[ranking_store.RankedElo]:
    """Keep the rows that belong to the requested circuit and region.

    :param rows: Candidate rating rows.
    :param circuit_sets: Circuits each team has played, keyed by team ID.
    :param circuit: Circuit a team must have played, or None to keep every circuit.
    :param region: Region a team must be classified in, or None to keep every region.
    :return: Rows matching both filters.
    """
    return [
        row
        for row in rows
        if (circuit is None or circuit in circuit_sets.get(row.elo.team_id, set()))
        and (region is None or row.elo.region == region)
    ]


async def rank_teams(session: AsyncSession, query: schemas.RankingQuery) -> schemas.RankingListResponse:
    """Rank teams by stored Elo, optionally within one circuit and region.

    A team is eligible with ``query.min_matches`` series inside the 180-day
    window and, unless ``include_inactive`` is set, a match within the last 90
    days. ``rank`` is the position among the returned selection; ``overall_rank``
    the position when the circuit and region filters are ignored.

    :param session: Caller-owned database session.
    :param query: Validated ranked-list query.
    :return: Paginated ranking.
    """
    as_of = ranking_date()
    active_since = None if query.include_inactive else window_start(as_of)
    by_team = await ranking_store.team_circuits(session, active_since)
    circuit_sets = {team_id: {row.circuit for row in rows} for team_id, rows in by_team.items()}
    window_counts = await ranking_store.series_window_counts(session, window_start(as_of), as_of)
    rows = sorted(
        await ranking_store.ranked_elos(session),
        key=lambda row: _sort_key(row, query.sort),
        reverse=query.order == RankingOrder.DESC,
    )
    eligible = [
        row
        for row in rows
        if window_counts.get(row.elo.team_id, 0) >= query.min_matches
        and (query.include_inactive or _is_active(row.elo, as_of))
    ]
    overall_ranks = _competition_ranks(eligible, query.sort)
    selected = _filter_rows(eligible, circuit_sets, query.circuit, query.region)
    ranks = _competition_ranks(selected, query.sort)
    page = selected[query.offset : query.offset + query.limit]
    items = []
    for row in page:
        circuits = by_team.get(row.elo.team_id, [])
        items.append(
            schemas.TeamRankingItem(
                rank=ranks[row.elo.team_id],
                overall_rank=overall_ranks[row.elo.team_id],
                team=_team_summary(row.team, row.elo.region),
                elo=round(row.elo.match_elo),
                map_elo=round(row.elo.map_elo),
                matches=_stats(row.elo.matches, row.elo.match_wins),
                maps=_stats(row.elo.maps, row.elo.map_wins),
                last_played_on=row.elo.last_played_on,
                primary_circuit=_primary_circuit(circuits),
                circuits=[circuit_row.circuit for circuit_row in circuits],
                region=row.elo.region,
            )
        )
    return schemas.RankingListResponse(
        as_of=as_of,
        algorithm=ELO_ALGORITHM,
        circuit=query.circuit or "all",
        region=query.region or "all",
        total=len(selected),
        limit=query.limit,
        offset=query.offset,
        teams=items,
    )


async def _recent_items(session: AsyncSession, team_id: str, limit: int) -> list[schemas.RecentMatchItem]:
    """Read a team's recent series with their opponents and events.

    :param session: Caller-owned database session.
    :param team_id: Team ID.
    :param limit: Maximum series to return.
    :return: Recent series, newest first.
    """
    rows = await ranking_store.recent_results(session, team_id, limit)
    opponent_ids = {row.result.team_b_id if row.result.team_a_id == team_id else row.result.team_a_id for row in rows}
    opponents = await ranking_store.teams_by_id(session, opponent_ids)
    items = []
    for row in rows:
        result, match, event = row.result, row.match, row.event
        team_is_a = result.team_a_id == team_id
        opponent = opponents.get(result.team_b_id if team_is_a else result.team_a_id)
        if opponent is None:
            continue
        items.append(
            schemas.RecentMatchItem(
                match_id=match.id,
                played_on=result.played_on,
                event=event.name,
                stage=match.stage,
                opponent=_team_summary(opponent),
                team_score=match.team_a_score if team_is_a else match.team_b_score,
                opponent_score=match.team_b_score if team_is_a else match.team_a_score,
                won=result.winner_team_id == team_id,
            )
        )
    return items


async def team_profile(session: AsyncSession, team_id: str) -> schemas.TeamRankingProfileResponse:
    """Read a team's Elo, ranks, circuit activity, form, and recent series.

    ``rank``, ``circuit_rank``, and ``region_rank`` follow the default ranked
    list: at least ``RANKING_MIN_MATCHES`` series inside the 180-day window and
    activity within the last 90 days.

    :param session: Caller-owned database session.
    :param team_id: Team ID.
    :return: Team ranking profile.
    :raises NotFoundError: If the team is unknown.
    """
    team = await session.get(Team, team_id)
    if team is None:
        raise NotFoundError("Team not found")
    as_of = ranking_date()
    elo = await session.get(TeamElo, team_id) or _new_team_elo(team_id)
    by_team = await ranking_store.team_circuits(session)
    circuit_sets = {entity_id: {row.circuit for row in rows} for entity_id, rows in by_team.items()}
    circuits = by_team.get(team_id, [])
    primary = _primary_circuit(circuits)
    rows = sorted(await ranking_store.ranked_elos(session), key=lambda row: row.elo.match_elo, reverse=True)
    window_counts = await ranking_store.series_window_counts(session, window_start(as_of), as_of)
    eligible = [
        row
        for row in rows
        if window_counts.get(row.elo.team_id, 0) >= RANKING_MIN_MATCHES and _is_active(row.elo, as_of)
    ]
    rank = _competition_ranks(eligible, RankingSort.ELO).get(team_id)
    circuit_rank = None
    if rank is not None and primary is not None:
        circuit_rank = _competition_ranks(_filter_rows(eligible, circuit_sets, primary), RankingSort.ELO).get(team_id)
    region = elo.region
    region_rank = None
    if rank is not None and region is not None:
        region_rank = _competition_ranks(_filter_rows(eligible, circuit_sets, region=region), RankingSort.ELO).get(
            team_id
        )
    recent = await _recent_items(session, team_id, 10)
    return schemas.TeamRankingProfileResponse(
        team=_team_summary(team, region),
        rank=rank,
        circuit_rank=circuit_rank,
        region=region,
        region_rank=region_rank,
        elo=round(elo.match_elo),
        map_elo=round(elo.map_elo),
        matches=_stats(elo.matches, elo.match_wins),
        maps=_stats(elo.maps, elo.map_wins),
        first_played_on=elo.first_played_on,
        last_played_on=elo.last_played_on,
        active=_is_active(elo, as_of),
        circuits=[
            schemas.TeamCircuitSummary(circuit=row.circuit, matches=row.matches, last_played_on=row.last_played_on)
            for row in circuits
        ],
        form="".join("W" if item.won else "L" for item in recent),
        recent=recent,
    )


async def predict(session: AsyncSession, team_a_id: str, team_b_id: str) -> schemas.PredictResponse:
    """Predict a match and its maps, preferring the configured prediction service.

    Map probability comes from the two teams' stored map Elo. Both estimates
    expose their source, and model warnings remain visible to callers.

    :param session: Caller-owned database session.
    :param team_a_id: First team's ID.
    :param team_b_id: Second team's ID.
    :return: Win probabilities and head-to-head history.
    :raises NotFoundError: If either team is unknown.
    """
    teams = await ranking_store.teams_by_id(session, [team_a_id, team_b_id])
    if team_a_id not in teams or team_b_id not in teams:
        raise NotFoundError("Team not found")
    as_of = ranking_date()
    elos = await ranking_store.elos(session, [team_a_id, team_b_id])
    elo_a = elos.get(team_a_id) or _new_team_elo(team_a_id)
    elo_b = elos.get(team_b_id) or _new_team_elo(team_b_id)
    model_prediction = await predictions.predict_match(team_a_id, team_b_id, as_of)
    if isinstance(model_prediction, PredictionFallbackReason):
        fallback_reason = model_prediction
        match_a = expected(elo_a.match_elo, elo_b.match_elo)
        match_probabilities = schemas.WinProbabilities(
            team_a=match_a,
            team_b=1.0 - match_a,
            source=EloPredictionSource(rating=PredictionRating.SERIES),
            warnings=[EloFallbackWarning(reason=fallback_reason)],
        )
    else:
        match_probabilities = model_prediction
    map_a = expected(elo_a.map_elo, elo_b.map_elo)
    match_rows, map_rows = await ranking_store.head_to_head_results(session, team_a_id, team_b_id)
    return schemas.PredictResponse(
        as_of=as_of,
        team_a=_elo_summary(teams[team_a_id], elo_a),
        team_b=_elo_summary(teams[team_b_id], elo_b),
        match=match_probabilities,
        map=schemas.WinProbabilities(
            team_a=map_a,
            team_b=1.0 - map_a,
            source=EloPredictionSource(rating=PredictionRating.MAP),
            warnings=[],
        ),
        head_to_head=schemas.HeadToHeadSummary(
            matches=len(match_rows),
            team_a_wins=sum(row.winner_team_id == team_a_id for row in match_rows),
            team_b_wins=sum(row.winner_team_id == team_b_id for row in match_rows),
            maps=len(map_rows),
            team_a_map_wins=sum(row.winner_team_id == team_a_id for row in map_rows),
            team_b_map_wins=sum(row.winner_team_id == team_b_id for row in map_rows),
            last_played_on=max((row.played_on for row in match_rows), default=None),
        ),
    )


def _elo_summary(team: Team, elo: TeamElo) -> schemas.TeamEloSummary:
    """Build a prediction side from a stored team and its ratings.

    :param team: Stored team row.
    :param elo: Stored Elo row.
    :return: Team identity with its ratings.
    """
    return schemas.TeamEloSummary(
        **_team_summary(team, elo.region).model_dump(),
        elo=round(elo.match_elo),
        map_elo=round(elo.map_elo),
        matches=_stats(elo.matches, elo.match_wins),
        maps=_stats(elo.maps, elo.map_wins),
        last_played_on=elo.last_played_on,
    )


def needs_repair(listed: schemas.Match, stored: ranking_store.StoredListing | None, now: int | None = None) -> bool:
    """Whether a final listing is missing from the ledger or contradicts it.

    An incomplete stored match is fetched again, but not on every cron tick: the
    fetch waits out a cooldown, and a match old enough that its page never produced
    maps is left as stored instead of polled forever. Both checks need ``now``.

    :param listed: Final match from the VLR listing.
    :param stored: What the ledger holds for its ID, or None when unknown.
    :param now: Unix time the listing was read; without it an incomplete match is fetched immediately.
    :return: True when the match page should be fetched and upserted.
    """
    if stored is None:
        return True
    if not stored.complete:
        if now is not None:
            today = datetime.fromtimestamp(now, _MATCH_TZ).date()
            if stored.maps == 0 and stored.played_on < today - timedelta(days=REPAIR_GIVE_UP_DAYS):
                return False
            if now - stored.ingested_at < REPAIR_COOLDOWN_SECONDS:
                return False
        return True
    if listed.team1.id is None or listed.team2.id is None:
        return False
    listed_sides = {(listed.team1.id, listed.team1.score), (listed.team2.id, listed.team2.score)}
    stored_sides = {(stored.team_a_id, stored.team_a_score), (stored.team_b_id, stored.team_b_score)}
    return listed_sides != stored_sides


def _update_match(
    stored: MatchRecord,
    details: schemas.MatchWithDetails,
    played_on: date,
    team_a: TeamWithImage,
    team_b: TeamWithImage,
) -> bool:
    """Overwrite the stored match fields the fetched page now shows.

    A field the page does not carry leaves the stored value alone, so a mostly
    rendered page cannot blank out a complete earlier fetch.

    :param stored: Stored match row, updated in place.
    :param details: Parsed match page.
    :param played_on: Date played in the match timezone.
    :param team_a: First parsed team.
    :param team_b: Second parsed team.
    :return: True when any field changed.
    """
    fields: dict[str, object] = {
        "event_id": details.event.id,
        "stage": details.event.stage if details.event.stage is not None else stored.stage,
        "played_on": played_on,
        "team_a_id": team_a.id if team_a.id is not None else stored.team_a_id,
        "team_b_id": team_b.id if team_b.id is not None else stored.team_b_id,
        "team_a_score": team_a.score if team_a.score is not None else stored.team_a_score,
        "team_b_score": team_b.score if team_b.score is not None else stored.team_b_score,
        "patch": details.event.patch if details.event.patch is not None else stored.patch,
    }
    changed = any(getattr(stored, name) != value for name, value in fields.items())
    for name, value in fields.items():
        setattr(stored, name, value)
    stored.ingested_at = int(time.time())
    return changed


async def ingest_match(
    session: AsyncSession, match_id: str, details: schemas.MatchWithDetails, circuit: Circuit
) -> bool:
    """Upsert one completed match and its maps from a parsed match page.

    The ratings are not touched here; the cron rebuilds them from the stored
    matches once the run's upserts are committed, so the fetch order is
    irrelevant.

    :param session: Caller-owned database session.
    :param match_id: Match ID.
    :param details: Parsed match page.
    :param circuit: Circuit the match's event belongs to.
    :return: True when a match or map was added or changed.
    """
    played_on = details.event.date.astimezone(_MATCH_TZ).date() if details.event.date else None
    if played_on is None:
        logger.warning("match %s has no date; not ingesting", match_id)
        return False
    if len(details.teams) != 2:
        logger.warning("match %s has %d teams; not ingesting", match_id, len(details.teams))
        return False
    team_a, team_b = details.teams
    for team in (team_a, team_b):
        if team.id:
            await scrape_store.upsert_team(session, team.id, name=team.name, tag=team.tag, logo=str(team.img))
    await ranking_store.upsert_event(session, details.event.id, details.event.series, circuit)
    maps = [
        (item.number - 1, item.map, item.teams[0].score, item.teams[1].score)
        for item in details.data
        if len(item.teams) == 2
        and item.teams[0].score is not None
        and item.teams[1].score is not None
        and item.teams[0].score != item.teams[1].score
    ]
    stored = await session.get(MatchRecord, match_id)
    if stored is None:
        session.add(
            MatchRecord(
                id=match_id,
                event_id=details.event.id,
                stage=details.event.stage,
                played_on=played_on,
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                team_a_score=team_a.score,
                team_b_score=team_b.score,
                patch=details.event.patch,
                source="vlr",
                ingested_at=int(time.time()),
            )
        )
        await session.flush()
        session.add_all(
            [
                MapRecord(match_id=match_id, map_index=index, name=name, team_a_score=score_a, team_b_score=score_b)
                for index, name, score_a, score_b in maps
            ]
        )
        return True
    changed = _update_match(stored, details, played_on, team_a, team_b)
    for index, name, score_a, score_b in maps:
        stored_map = await session.get(MapRecord, (match_id, index))
        if stored_map is None:
            session.add(
                MapRecord(match_id=match_id, map_index=index, name=name, team_a_score=score_a, team_b_score=score_b)
            )
            changed = True
        elif (stored_map.name, stored_map.team_a_score, stored_map.team_b_score) != (name, score_a, score_b):
            stored_map.name, stored_map.team_a_score, stored_map.team_b_score = name, score_a, score_b
            changed = True
    expected_maps = (stored.team_a_score or 0) + (stored.team_b_score or 0)
    if maps and len(maps) == expected_maps:
        obsolete = await session.execute(
            delete(MapRecord)
            .where(
                MapRecord.match_id == match_id,
                MapRecord.map_index > max(index for index, *_ in maps),
            )
            .returning(MapRecord.map_index)
        )
        if obsolete.first() is not None:
            changed = True
    return changed


async def resolve_event_circuit(session: AsyncSession, event_id: str, name: str) -> Circuit:
    """Resolve an event's circuit from its stored record or the source's tier listings.

    A resolved circuit is stored with its check time, so an event listed
    under no ranked tier is only crawled once.

    :param session: Caller-owned database session.
    :param event_id: Event ID.
    :param name: Event title, used when the event is not stored yet.
    :return: The event's circuit.
    """
    stored = await session.get(EventRecord, event_id)
    if stored is not None and stored.circuit_checked_at is not None:
        return stored.circuit
    circuit = (await tier_event_circuits(stop_ids={event_id})).get(event_id, Circuit.OTHER)
    await ranking_store.set_event_circuit(
        session, event_id, stored.name if stored is not None else name, circuit, int(time.time())
    )
    return circuit
