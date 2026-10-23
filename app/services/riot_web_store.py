"""Reads and upserts for ``riot_web_matches`` and the VLR ids its links resolve to. Never commits; the caller owns the transaction."""

import logging
from collections.abc import Sequence
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import RIOT_WEB_DETAILS_REFRESH, RIOT_WEB_DETAILS_SETTLE, RIOT_WEB_STANDINGS_WINDOW
from app.db.models import MatchRecord, RiotWebMatch, RiotWebTournament, Team
from app.schemas.riot_web import (
    LiveEvent,
    LiveMatch,
    ScheduleEvent,
    ScheduleMatch,
    StoredDetails,
    StoredTeam,
    Tournament,
)
from app.services.riot_web_link import Link, RiotCandidate

logger = logging.getLogger(__name__)


def _stored_teams(match: ScheduleMatch | LiveMatch) -> list[dict]:
    return [StoredTeam(name=team.name.strip()).model_dump(mode="json") for team in match.teams]


def _row(event: ScheduleEvent | LiveEvent) -> dict | None:
    """Build one row from a schedule or live event, or None when it cannot supply one."""
    match = event.match
    if match is None or event.start_time is None or event.league is None:
        return None
    strategy = getattr(match, "strategy", None)
    return {
        "riot_id": match.id,
        "start_time": event.start_time,
        "league_name": event.league.name,
        "best_of": strategy.count if strategy else None,
        "teams": _stored_teams(match),
    }


async def upsert_matches(session: AsyncSession, events: Sequence[ScheduleEvent]) -> int:
    """Store or refresh each schedule event that has a match, keeping its link columns untouched.

    :param session: Caller-owned database session.
    :param events: Schedule events.
    :return: How many rows were written.
    """
    rows = {}
    for event in events:
        if (row := _row(event)) is not None:
            rows[row["riot_id"]] = row
    if not rows:
        return 0
    statement = insert(RiotWebMatch).values(list(rows.values()))
    statement = statement.on_conflict_do_update(
        index_elements=[RiotWebMatch.riot_id],
        set_={
            column: getattr(statement.excluded, column) for column in ("start_time", "league_name", "best_of", "teams")
        },
    )
    await session.execute(statement)
    return len(rows)


async def insert_missing(session: AsyncSession, events: Sequence[LiveEvent]) -> None:
    """Insert live-list series the table does not hold yet, leaving every existing row untouched.

    Live discovery persists a link by updating the row, so a series the schedule has not reached
    needs its row first. ``on_conflict_do_nothing`` keeps the schedule's established metadata when
    the row already exists, including under a concurrent schedule sync.

    :param session: Caller-owned database session.
    :param events: Live events eligible for a new link.
    :return: None.
    """
    rows = {}
    for event in events:
        if (row := _row(event)) is not None:
            rows[row["riot_id"]] = row
    if not rows:
        return
    await session.execute(
        insert(RiotWebMatch).values(list(rows.values())).on_conflict_do_nothing(index_elements=[RiotWebMatch.riot_id])
    )


async def unlinked_between(session: AsyncSession, start: datetime, end: datetime) -> list[RiotCandidate]:
    """Read the unlinked Riot series starting in a window.

    :param session: Caller-owned database session.
    :param start: Earliest start time, inclusive.
    :param end: Latest start time, inclusive.
    :return: The candidates linking may claim.
    """
    rows = await session.execute(
        select(RiotWebMatch.riot_id, RiotWebMatch.start_time, RiotWebMatch.teams, RiotWebMatch.league_name).where(
            RiotWebMatch.vlr_match_id.is_(None),
            RiotWebMatch.start_time >= start,
            RiotWebMatch.start_time <= end,
        )
    )
    return [
        RiotCandidate(
            riot_id=riot_id,
            start_time=start_time,
            teams=(teams[0]["name"], teams[1]["name"]),
            league_name=league_name,
        )
        for riot_id, start_time, teams, league_name in rows.all()
        if len(teams) == 2
    ]


async def linked_vlr_ids(session: AsyncSession) -> set[str]:
    """List every VLR match a Riot series is already linked to.

    :param session: Caller-owned database session.
    :return: The claimed VLR match IDs.
    """
    ids = await session.scalars(select(RiotWebMatch.vlr_match_id).where(RiotWebMatch.vlr_match_id.is_not(None)))
    return {match_id for match_id in ids if match_id}


async def vlr_ids_for(session: AsyncSession, riot_ids: Sequence[str]) -> dict[str, str]:
    """Resolve Riot series ids to the VLR matches they are linked to.

    :param session: Caller-owned database session.
    :param riot_ids: Riot series ids to resolve.
    :return: Riot id to VLR match id, for the ids that have a link.
    """
    if not riot_ids:
        return {}
    rows = await session.execute(
        select(RiotWebMatch.riot_id, RiotWebMatch.vlr_match_id).where(
            RiotWebMatch.riot_id.in_(riot_ids), RiotWebMatch.vlr_match_id.is_not(None)
        )
    )
    return {riot_id: vlr_match_id for riot_id, vlr_match_id in rows.all() if vlr_match_id}


async def save_links(session: AsyncSession, links: Sequence[Link]) -> int:
    """Write each link, but only while the row is still unlinked.

    Two writers can pick the same VLR match from before either commits, and the unique
    constraint then rejects the loser. Each link gets a savepoint, so that rejection skips
    only its own link and keeps the rest of the batch.

    :param session: Caller-owned database session.
    :param links: Links from :func:`app.services.riot_web_link.link`.
    :return: How many links were written.
    """
    written = 0
    for link in links:
        try:
            async with session.begin_nested():
                result = await session.execute(
                    update(RiotWebMatch)
                    .where(RiotWebMatch.riot_id == link.riot_id, RiotWebMatch.vlr_match_id.is_(None))
                    .values(vlr_match_id=link.vlr_match_id, vlr_event_id=link.vlr_event_id)
                    .returning(RiotWebMatch.riot_id)
                )
                written += len(result.all())
        except IntegrityError:
            logger.warning("VLR match %s is already linked; skipping Riot series %s", link.vlr_match_id, link.riot_id)
    return written


async def details_due(session: AsyncSession, now: datetime, limit: int) -> list[str]:
    """List the linked series whose details the next run should fetch.

    A linked series is due once before it has ever been fetched, then again an hour after each
    fetch while it is under ``RIOT_WEB_DETAILS_SETTLE`` old. Newest first, because a recent
    series is the one still gaining VODs.

    :param session: Caller-owned database session.
    :param now: When the run started.
    :param limit: Most ids to return.
    :return: Riot ids to fetch.
    """
    statement = (
        select(RiotWebMatch.riot_id)
        .where(
            RiotWebMatch.vlr_match_id.is_not(None),
            or_(
                RiotWebMatch.details_fetched_at.is_(None),
                and_(
                    RiotWebMatch.details_fetched_at < now - RIOT_WEB_DETAILS_REFRESH,
                    RiotWebMatch.start_time > now - RIOT_WEB_DETAILS_SETTLE,
                ),
            ),
        )
        .order_by(RiotWebMatch.start_time.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def save_details(session: AsyncSession, riot_id: str, details: StoredDetails | None, now: datetime) -> None:
    """Store one series' details, keeping the tournament id when Riot no longer serves it.

    :param session: Caller-owned database session.
    :param riot_id: Riot's series id.
    :param details: Details to store, or None when Riot answered ``event: null``.
    :param now: When the fetch happened, so the refresh rule counts from it.
    :return: None.
    """
    values = {"details": details.model_dump(mode="json") if details else None, "details_fetched_at": now}
    if details is not None:
        values["tournament_id"] = details.tournament_id
    await session.execute(update(RiotWebMatch).where(RiotWebMatch.riot_id == riot_id).values(**values))


async def details_for_vlr(session: AsyncSession, vlr_match_id: str) -> StoredDetails | None:
    """Read the stored details of the Riot series linked to one VLR match.

    :param session: Caller-owned database session.
    :param vlr_match_id: VLR match id.
    :return: The details, or None when the match is unlinked, never fetched, or its row is unreadable.
    """
    raw = await session.scalar(select(RiotWebMatch.details).where(RiotWebMatch.vlr_match_id == vlr_match_id))
    if raw is None:
        return None
    try:
        return StoredDetails.model_validate(raw)
    except ValidationError:
        logger.warning("stored Riot web details for VLR match %s are unreadable", vlr_match_id, exc_info=True)
        return None


async def tournaments_for_event(session: AsyncSession, vlr_event_id: str) -> list[str]:
    """List the Riot tournaments linked to a VLR event.

    A tournament can span several VLR events, and one VLR event can hold several tournaments, so the
    relation is many-to-many on purpose.

    :param session: Caller-owned database session.
    :param vlr_event_id: VLR event id.
    :return: Distinct Riot tournament ids, in no particular order.
    """
    rows = await session.scalars(
        select(RiotWebMatch.tournament_id)
        .outerjoin(MatchRecord, MatchRecord.id == RiotWebMatch.vlr_match_id)
        .where(
            RiotWebMatch.tournament_id.is_not(None),
            or_(RiotWebMatch.vlr_event_id == vlr_event_id, MatchRecord.event_id == vlr_event_id),
        )
        .distinct()
    )
    return [tournament_id for tournament_id in rows if tournament_id]


async def active_tournaments(session: AsyncSession, now: datetime) -> list[str]:
    """List tournaments whose linked series are close enough to now to be worth refreshing.

    :param session: Caller-owned database session.
    :param now: Current time.
    :return: Distinct tournament ids.
    """
    rows = await session.scalars(
        select(RiotWebMatch.tournament_id)
        .where(
            RiotWebMatch.tournament_id.is_not(None),
            RiotWebMatch.vlr_match_id.is_not(None),
            RiotWebMatch.start_time >= now - RIOT_WEB_STANDINGS_WINDOW,
            RiotWebMatch.start_time <= now + RIOT_WEB_STANDINGS_WINDOW,
        )
        .distinct()
    )
    return [tournament_id for tournament_id in rows if tournament_id]


async def stored_tournament(session: AsyncSession, tournament_id: str) -> RiotWebTournament | None:
    """Read one stored tournament.

    :param session: Caller-owned database session.
    :param tournament_id: Riot tournament id.
    :return: The row, or None when it was never fetched.
    """
    return await session.get(RiotWebTournament, tournament_id)


async def save_tournament(
    session: AsyncSession, tournament_id: str, tournament: Tournament | None, now: datetime
) -> None:
    """Store one tournament's layout as Riot returned it, or NULL when it has none.

    :param session: Caller-owned database session.
    :param tournament_id: Riot tournament id.
    :param tournament: Riot's layout, or None for an empty answer.
    :param now: When the fetch happened.
    :return: None.
    """
    values = {
        "tournament_id": tournament_id,
        "standings": tournament.model_dump(mode="json", by_alias=True) if tournament else None,
        "fetched_at": now,
    }
    statement = insert(RiotWebTournament).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[RiotWebTournament.tournament_id],
        set_={column: getattr(statement.excluded, column) for column in values if column != "tournament_id"},
    )
    await session.execute(statement)


async def matches_by_riot_id(session: AsyncSession, riot_ids: Sequence[str]) -> dict[str, RiotWebMatch]:
    """Read the stored series for a set of Riot ids.

    :param session: Caller-owned database session.
    :param riot_ids: Riot series ids.
    :return: Riot series id mapped to its row; missing ids are absent.
    """
    if not riot_ids:
        return {}
    rows = await session.scalars(select(RiotWebMatch).where(RiotWebMatch.riot_id.in_(riot_ids)))
    return {row.riot_id: row for row in rows}


async def events_by_riot_id(session: AsyncSession, riot_ids: Sequence[str]) -> dict[str, str]:
    """Resolve each linked Riot series to the VLR event its match belongs to.

    Mirrors :func:`tournaments_for_event`: the link's event is used when present, otherwise the
    ledger's event for the linked match.

    :param session: Caller-owned database session.
    :param riot_ids: Riot series ids.
    :return: Riot series id mapped to its VLR event id; unlinked ids are absent.
    """
    if not riot_ids:
        return {}
    rows = await session.execute(
        select(RiotWebMatch.riot_id, RiotWebMatch.vlr_event_id, MatchRecord.event_id)
        .outerjoin(MatchRecord, MatchRecord.id == RiotWebMatch.vlr_match_id)
        .where(RiotWebMatch.riot_id.in_(riot_ids), RiotWebMatch.vlr_match_id.is_not(None))
    )
    return {
        riot_id: link_event or ledger_event
        for riot_id, link_event, ledger_event in rows.all()
        if link_event or ledger_event
    }


async def events_by_tournament(session: AsyncSession, tournament_ids: Sequence[str]) -> dict[str, set[str]]:
    """Resolve each linked Riot tournament to every VLR event its links name.

    This is the evidence a bracket section without linked matches is judged by: a tournament that
    names one VLR event only cannot be showing another event's bracket.

    :param session: Caller-owned database session.
    :param tournament_ids: Riot tournament ids.
    :return: Riot tournament id mapped to its VLR event ids; tournaments with no links are absent.
    """
    if not tournament_ids:
        return {}
    rows = await session.execute(
        select(RiotWebMatch.tournament_id, RiotWebMatch.vlr_event_id, MatchRecord.event_id)
        .outerjoin(MatchRecord, MatchRecord.id == RiotWebMatch.vlr_match_id)
        .where(RiotWebMatch.tournament_id.in_(tournament_ids), RiotWebMatch.vlr_match_id.is_not(None))
    )
    events: dict[str, set[str]] = {}
    for tournament_id, link_event, ledger_event in rows.all():
        if tournament_id and (event_id := link_event or ledger_event):
            events.setdefault(tournament_id, set()).add(event_id)
    return events


async def riot_team_fields(session: AsyncSession) -> list[tuple[str, str | None, str | None]]:
    """Read the teams that carry Riot's own name and code.

    ``scripts.mirror_team_logos`` writes them after matching a Riot team to its VLR team, so they
    are a confirmed pairing rather than a name guess.

    :param session: Caller-owned database session.
    :return: (VLR team id, Riot name, Riot code) rows, only for the mirrored teams.
    """
    rows = await session.execute(
        select(Team.id, Team.riot_name, Team.riot_code).where(
            or_(Team.riot_name.is_not(None), Team.riot_code.is_not(None))
        )
    )
    return list(rows.all())
