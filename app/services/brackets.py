"""Riot's bracket layout for a VLR event, stored as Riot returns it and converted on read.

Riot's ``getStandings`` answer is a layout, not a round tree: ``columns`` are topological layers,
``cells`` are display groups that may hold several matches or mix bracket sides, and a slot's
``origin`` is the only progression edge. This module returns that layout faithfully and does not
infer rounds, sides, formats or byes, because every one of those has counter-examples.
"""

import json
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx2
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError

from app import cache
from app.constants import (
    CACHE_TTL_RIOT_WEB_BRACKETS,
    RIOT_WEB_BRACKETS_KEY,
    RIOT_WEB_REQUEST_TIMEOUT,
    RIOT_WEB_STANDINGS_RETRY,
)
from app.core import connections
from app.core.config import settings
from app.db.models import RiotWebMatch
from app.enums import BracketSourceKind, BracketSourceResult, BracketStatus, MatchStatus, RiotMatchState
from app.schemas.brackets import (
    BracketCell,
    BracketColumn,
    BracketMatch,
    BracketSection,
    BracketSlot,
    BracketSource,
    BracketTeam,
    EventBrackets,
)
from app.schemas.riot_web import StandingsMatch, StandingsOrigin, StandingsTeam, Tournament
from app.services import riot_web, riot_web_store

logger = logging.getLogger(__name__)

_STATUS = {
    RiotMatchState.UNSTARTED: MatchStatus.UPCOMING,
    RiotMatchState.IN_PROGRESS: MatchStatus.LIVE,
    RiotMatchState.COMPLETED: MatchStatus.COMPLETED,
}


def _fold(value: str | None) -> str:
    """Fold a team name or code to a comparison key.

    :param value: Riot's team name or code.
    :return: Lowercase ASCII words, or an empty string when there is no value.
    """
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def _vlr_team_ids(rows: Sequence[tuple[str, str | None, str | None]]) -> dict[str, str]:
    """Index each Riot name and code to the VLR team it was matched to.

    A name or code that two VLR teams carry is dropped: the app links to a team, so a guess here
    would link to the wrong one.

    :param rows: (VLR team id, Riot name, Riot code) rows.
    :return: Folded Riot name or code mapped to its one VLR team id.
    """
    seen: dict[str, set[str]] = {}
    for team_id, riot_name, riot_code in rows:
        for key in (riot_name, riot_code):
            if folded := _fold(key):
                seen.setdefault(folded, set()).add(team_id)
    return {key: next(iter(ids)) for key, ids in seen.items() if len(ids) == 1}


def _team(team: StandingsTeam, vlr_teams: dict[str, str]) -> BracketTeam | None:
    """Build a display team; Riot's TBD team id is ``"0"``.

    ``vlr_team_id`` is set only when Riot's code and name agree on one VLR team, or only one of
    them is known: a Riot team the id mirror never matched keeps ``None`` rather than a guess.

    :param team: Riot's team entry.
    :param vlr_teams: Folded Riot name or code mapped to its VLR team id.
    :return: The team, or None for a TBD slot.
    """
    if team.id == "0":
        return None
    image = str(team.image) if team.image else None
    if image and image.startswith("http://"):
        image = f"https://{image.removeprefix('http://')}"
    by_code = vlr_teams.get(_fold(team.code))
    by_name = vlr_teams.get(_fold(team.name))
    vlr_team_id = by_code or by_name
    if by_code and by_name and by_code != by_name:
        vlr_team_id = None  # the two fields name different teams, so neither is trusted
    return BracketTeam(id=team.id, name=team.name.strip(), code=team.code.strip(), img=image, vlr_team_id=vlr_team_id)


def _source(origin: StandingsOrigin | None, structural_ids: dict[str, str]) -> BracketSource | None:
    """Turn a slot's entry edge into a match or seed source.

    :param origin: Riot's per-team ``origin``.
    :param structural_ids: Section-local ``structuralId`` to Riot match id.
    :return: The source, or None when Riot gave none.
    """
    if origin is None:
        return None
    if origin.type == "match":
        return BracketSource(
            kind=BracketSourceKind.MATCH,
            match_id=structural_ids.get(origin.structural_id),
            result=BracketSourceResult.WINNER if origin.slot == 1 else BracketSourceResult.LOSER,
        )
    return BracketSource(kind=BracketSourceKind.SEED, seed=origin.slot)


def _match(
    match: StandingsMatch,
    structural_ids: dict[str, str],
    link: RiotWebMatch | None,
    vlr_teams: dict[str, str],
) -> BracketMatch:
    """Convert one Riot series, keeping its two slots in display order.

    :param match: Riot's match entry.
    :param structural_ids: Section-local ``structuralId`` to Riot match id.
    :param link: The stored series, when the schedule holds it.
    :param vlr_teams: Folded Riot name or code mapped to its VLR team id.
    :return: The public match.
    """
    status = _STATUS.get(match.state, MatchStatus.TBD)
    settled = status in (MatchStatus.COMPLETED, MatchStatus.LIVE)
    slots = [
        BracketSlot(
            team=_team(team, vlr_teams),
            source=_source(team.origin, structural_ids),
            score=team.result.game_wins if settled and team.result else None,
            winner=(team.result.outcome == "win") if settled and team.result else None,
        )
        for team in match.teams
    ]
    return BracketMatch(
        id=match.id,
        vlr_match_id=link.vlr_match_id if link else None,
        label=match.description or None,
        status=status,
        start_time=link.start_time if link else None,
        best_of=link.best_of if link else None,
        slots=slots,
    )


def _owned(owners: set[str], tournament_events: set[str], event_id: str) -> bool:
    """Decide whether one section is the requested event's to show.

    :param owners: VLR events named by the section's linked matches.
    :param tournament_events: VLR events every linked match in the tournament names.
    :param event_id: VLR event the request is for.
    :return: Whether the section belongs to the request.
    """
    # With no linked matches, the whole tournament must name this event: a tournament shared by
    # several VLR events (Riot's Game Changers LATAM, for example) would otherwise serve another's.
    return (owners or tournament_events) == {event_id}


def sections_from(
    tournament: Tournament,
    links: dict[str, RiotWebMatch],
    *,
    event_id: str,
    match_events: dict[str, str],
    tournament_events: set[str],
    vlr_teams: dict[str, str],
) -> list[BracketSection]:
    """Convert the bracket sections owned by one event, preserving column, cell and match order.

    ``group`` and ``crossGroup`` sections are tables VLR's own standings already cover. A section
    with no ``match`` origin (Swiss, for example) renders with seed sources only.

    A section is owned by the VLR event its linked matches name; unlinked matches stay inside it. A
    section with no linked matches is kept only when the tournament is known to map to this event
    alone (see :func:`_owned`), so the same Riot tournament can serve several VLR events without
    showing one of them another's bracket.

    :param tournament: Riot's layout, already validated.
    :param links: Riot series id mapped to its stored row.
    :param event_id: VLR event the request is for.
    :param match_events: Riot series id mapped to the VLR event its link names.
    :param tournament_events: VLR events every linked match in the tournament names.
    :param vlr_teams: Folded Riot name or code mapped to its VLR team id.
    :return: One section per Riot ``type: bracket`` section owned by the event, in Riot's order.
    """
    sections = []
    for stage in tournament.stages:
        for section in stage.sections:
            if section.type != "bracket":
                continue
            owners = {
                match_events[match.id]
                for column in section.columns
                for cell in column.cells
                for match in cell.matches
                if match.id in match_events
            }
            if not _owned(owners, tournament_events, event_id):
                logger.info(
                    "withholding Riot bracket section %s for event %s: links name %s, tournament links name %s",
                    section.id,
                    event_id,
                    sorted(owners),
                    sorted(tournament_events),
                )
                continue
            structural_ids = {
                match.structural_id: match.id
                for column in section.columns
                for cell in column.cells
                for match in cell.matches
            }
            sections.append(
                BracketSection(
                    id=section.id,
                    tournament=tournament.name,
                    stage=stage.name,
                    name=section.name,
                    columns=[
                        BracketColumn(
                            cells=[
                                BracketCell(
                                    name=cell.name,
                                    matches=[
                                        _match(match, structural_ids, links.get(match.id), vlr_teams)
                                        for match in cell.matches
                                    ],
                                )
                                for cell in column.cells
                            ]
                        )
                        for column in section.columns
                    ],
                )
            )
    return sections


async def _fetch_tournament(tournament_id: str) -> Tournament | None:
    """Fetch one layout for the request path, after the read transaction has closed.

    :param tournament_id: Riot tournament id.
    :return: Riot's layout, or None when it has none.
    """
    async with riot_web.client() as client:
        return await riot_web.standings(client, tournament_id, timeout=RIOT_WEB_REQUEST_TIMEOUT)


@dataclass(frozen=True)
class _Brackets:
    """What one request could learn, and whether a source went unanswered."""

    sections: list[BracketSection]
    fetched_at: datetime | None  # oldest layout the returned sections came from
    incomplete: bool  # a source went unanswered, so no sections may mean "unknown", not "none"


async def _sections(event_id: str, redis: Redis | None) -> _Brackets:
    """Read or fetch each linked tournament, then convert the ones that parse.

    :param event_id: VLR event id.
    :param redis: Request Redis client.
    :return: Converted sections with their oldest fetch time, and whether any source was unanswered.
    """
    sessions = connections.subscription_sessions
    if sessions is None:
        return _Brackets([], None, True)

    now = datetime.now(UTC)
    try:
        async with sessions() as session:
            tournament_ids = await riot_web_store.tournaments_for_event(session, event_id)
            stored = {}
            for tournament_id in tournament_ids:
                if row := await riot_web_store.stored_tournament(session, tournament_id):
                    stored[tournament_id] = row
    except SQLAlchemyError:
        logger.warning("could not read Riot tournaments for event %s", event_id, exc_info=True)
        return _Brackets([], None, True)
    if not tournament_ids:
        # No link names Riot for this event, so whether Riot has a bracket is unknown, not "none".
        return _Brackets([], None, True)

    tournaments: dict[str, Tournament] = {}
    fetched_at: dict[str, datetime] = {}
    incomplete = False
    for tournament_id in tournament_ids:
        row = stored.get(tournament_id)
        tournament = None
        if row and row.standings is not None:
            try:
                tournament = Tournament.model_validate(row.standings)
            except ValidationError:
                logger.warning("stored Riot standings for tournament %s no longer parse; refetching", tournament_id)
        if tournament is not None:
            tournaments[tournament_id] = tournament
            fetched_at[tournament_id] = row.fetched_at
            continue
        # An empty answer stores NULL, so a tournament Riot has no layout for is not retried for a day.
        if row and row.standings is None and now - row.fetched_at < RIOT_WEB_STANDINGS_RETRY:
            continue
        try:
            tournament = await _fetch_tournament(tournament_id)
        except httpx2.HTTPStatusError as error:
            if redis is not None:
                await riot_web.report_rejection(redis, error)
            logger.warning("could not fetch Riot standings for tournament %s: %s", tournament_id, error)
            incomplete = True
            continue
        except httpx2.HTTPError, ValidationError, json.JSONDecodeError:
            logger.warning("could not fetch Riot standings for tournament %s", tournament_id, exc_info=True)
            incomplete = True
            continue
        try:
            async with sessions.begin() as session:
                await riot_web_store.save_tournament(session, tournament_id, tournament, now)
        except SQLAlchemyError:
            logger.warning("could not store Riot standings for tournament %s", tournament_id, exc_info=True)
        if tournament:
            tournaments[tournament_id] = tournament
            fetched_at[tournament_id] = now

    if not tournaments:
        return _Brackets([], None, incomplete)

    ordered = sorted(
        tournaments.items(),
        key=lambda item: item[1].start_time or datetime.max.replace(tzinfo=UTC),
    )
    match_ids = [
        match.id
        for tournament in tournaments.values()
        for stage in tournament.stages
        for section in stage.sections
        if section.type == "bracket"
        for column in section.columns
        for cell in column.cells
        for match in cell.matches
    ]
    if not match_ids:
        return _Brackets([], None, incomplete)
    try:
        async with sessions() as session:
            links = await riot_web_store.matches_by_riot_id(session, match_ids)
            match_events = await riot_web_store.events_by_riot_id(session, match_ids)
            tournament_events = await riot_web_store.events_by_tournament(session, tournament_ids)
            vlr_teams = _vlr_team_ids(await riot_web_store.riot_team_fields(session))
    except SQLAlchemyError:
        logger.warning("could not read Riot series links for event %s", event_id, exc_info=True)
        return _Brackets([], None, True)

    sections: list[BracketSection] = []
    oldest: datetime | None = None
    for tournament_id, tournament in ordered:
        converted = sections_from(
            tournament,
            links,
            event_id=event_id,
            match_events=match_events,
            tournament_events=tournament_events.get(tournament_id, set()),
            vlr_teams=vlr_teams,
        )
        if converted:
            sections.extend(converted)
            stamp = fetched_at[tournament_id]
            oldest = stamp if oldest is None else min(oldest, stamp)
    return _Brackets(sections, oldest, incomplete)


def _status(brackets: _Brackets) -> BracketStatus:
    """Name what the response's sections do and do not cover.

    :param brackets: What the request could learn.
    :return: The status the client should render.
    """
    if brackets.incomplete:
        return BracketStatus.STALE if brackets.sections else BracketStatus.UNAVAILABLE
    return BracketStatus.OK if brackets.sections else BracketStatus.NO_BRACKET


async def event_brackets(event_id: str, client: Redis | None = None) -> EventBrackets:
    """Return a VLR event's Riot brackets.

    An event with no Riot tournament, an unset key, or an unreachable Riot answers with no
    sections rather than an error: a mapping gap is not a client error. ``status`` says whether
    that empty answer means Riot has no bracket (``no_bracket``) or the answer is unknown
    (``unavailable``), and whether returned sections are incomplete (``stale``). A converted
    answer is cached for Riot's 60-second CDN window.

    :param event_id: VLR event id.
    :param client: Request Redis client.
    :return: The public bracket response.
    """
    if not settings.RIOT_WEB_API_KEY:
        return EventBrackets(event_id=event_id, status=BracketStatus.UNAVAILABLE)

    key = RIOT_WEB_BRACKETS_KEY.format(event_id)
    if raw := await cache.get(key, client=client):
        try:
            return EventBrackets.model_validate_json(raw)
        except ValidationError:
            # A short-lived converted payload: an old shape is a miss, never a 5xx.
            logger.warning("cached Riot brackets for event %s no longer parse; rebuilding", event_id)
    brackets = await _sections(event_id, client)
    result = EventBrackets(
        event_id=event_id,
        sections=brackets.sections,
        available=bool(brackets.sections),
        status=_status(brackets),
        fetched_at=brackets.fetched_at,
    )
    await cache.set(key, result.model_dump_json(), ttl=CACHE_TTL_RIOT_WEB_BRACKETS, client=client)
    return result


async def refresh_tournaments(sessions: riot_web.Sessions, redis: Redis) -> str | None:
    """Refresh the standings of Riot tournaments whose linked series are playing now.

    The details cron fills each linked series' ``tournament_id``; this run only reads it. Every
    tournament is fetched before any write, then written one per transaction, so no row lock is
    held across network I/O.

    :param sessions: Session factory for the subscription database.
    :param redis: Shared Redis client, for the key-rejection alert.
    :return: A one-line summary of the run, or None when nothing was due or a read failed.
    """
    now = datetime.now(UTC)
    try:
        async with sessions() as session:
            active = await riot_web_store.active_tournaments(session, now)
    except SQLAlchemyError:
        logger.warning("could not read the Riot tournaments due a standings refresh", exc_info=True)
        return None
    if not active:
        return None

    fetched: list[tuple[str, Tournament | None]] = []
    async with riot_web.client() as client:
        for tournament_id in active:
            try:
                tournament = await riot_web.standings(client, tournament_id)
            except httpx2.HTTPStatusError as error:
                await riot_web.report_rejection(redis, error)
                logger.warning("could not refresh Riot standings for tournament %s: %s", tournament_id, error)
                if riot_web.stops_run(error):
                    break
                continue
            except httpx2.HTTPError, ValidationError, json.JSONDecodeError:
                logger.warning("could not refresh Riot standings for tournament %s", tournament_id, exc_info=True)
                continue
            fetched.append((tournament_id, tournament))

    for tournament_id, tournament in fetched:
        try:
            async with sessions.begin() as session:
                await riot_web_store.save_tournament(session, tournament_id, tournament, now)
        except SQLAlchemyError:
            logger.warning("could not store Riot standings for tournament %s", tournament_id, exc_info=True)
    return f"{len(fetched)} tournaments refreshed"
