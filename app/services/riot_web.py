"""Riot's web persisted service: the client, its payloads, and the runs that keep them stored.

This is the watch-page API (``esports-api.service.valorantesports.com``), not the esports cluster
(:mod:`app.services.riot`). The refresh functions take the caller's session factory and open one
transaction per write; the single-payload helpers never open one.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import httpx2
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import schemas
from app.constants import (
    RIOT_WEB_API_URL,
    RIOT_WEB_DETAILS_BATCH,
    RIOT_WEB_KEY_ALERT_INTERVAL,
    RIOT_WEB_KEY_ALERTED_KEY,
    RIOT_WEB_LIVE_TIMEOUT,
    RIOT_WEB_PARAMS,
    RIOT_WEB_SCHEDULE_LOOKBACK,
    RIOT_WEB_SCHEDULE_MAX_PAGES,
    RIOT_WEB_TIMEOUT,
)
from app.core.config import settings
from app.enums import VodProvider
from app.schemas.riot_web import (
    EventDetails,
    EventEnvelope,
    LiveEvent,
    ScheduleEnvelope,
    ScheduleEvent,
    SchedulePage,
    StandingsEnvelope,
    StoredDetails,
    StoredGame,
    StoredVod,
    Tournament,
)
from app.services import riot_web_link, riot_web_store

logger = logging.getLogger(__name__)

Sessions = async_sessionmaker[AsyncSession]


def client() -> httpx2.AsyncClient:
    """Open a client for Riot's web persisted service.

    :return: An HTTP client carrying the site key, ``hl`` and ``sport`` on every request.
    """
    return httpx2.AsyncClient(
        base_url=RIOT_WEB_API_URL,
        params=RIOT_WEB_PARAMS,
        headers={"x-api-key": settings.RIOT_WEB_API_KEY or "", "User-Agent": "val-esports-backend"},
        timeout=RIOT_WEB_TIMEOUT,
    )


async def schedule_page(riot: httpx2.AsyncClient, page_token: str | None) -> SchedulePage:
    """Fetch one page of Riot's schedule.

    :param riot: Client from :func:`client`.
    :param page_token: Opaque ``pages.older`` cursor from the previous page, or None for the newest page.
    :return: The page's readable events and its older cursor. An unreadable event is logged and skipped.
    :raises httpx2.HTTPError: When Riot does not answer with the page.
    :raises pydantic.ValidationError: When the envelope is not a schedule.
    """
    response = await riot.get("getSchedule", params={"pageToken": page_token} if page_token else None)
    response.raise_for_status()
    schedule = ScheduleEnvelope.model_validate(response.json()).data.schedule
    events = []
    for item in schedule.events:
        try:
            events.append(ScheduleEvent.model_validate(item))
        except ValidationError:
            logger.warning("skipping an unreadable Riot web schedule event", exc_info=True)
    return SchedulePage(events=events, older=schedule.pages.older if schedule.pages else None)


async def live_events(riot: httpx2.AsyncClient) -> list[LiveEvent]:
    """Fetch the series Riot's site currently shows live.

    No populated answer has been captured, so each event is read leniently and one that does not
    validate costs only that event. The envelope is the schedule's, as the empty answer confirms.

    :param riot: Client from :func:`client`.
    :return: The readable live events; an event without a match is kept for the caller to skip.
    :raises httpx2.HTTPError: When Riot does not answer with its live list.
    :raises pydantic.ValidationError: When the answer is not the live-list envelope.
    """
    response = await riot.get("getLiveDetails", timeout=RIOT_WEB_LIVE_TIMEOUT)
    response.raise_for_status()
    schedule = ScheduleEnvelope.model_validate(response.json()).data.schedule
    events = []
    for item in schedule.events:
        try:
            events.append(LiveEvent.model_validate(item))
        except ValidationError:
            logger.warning("skipping an unreadable Riot web live event", exc_info=True)
    return events


async def event_details(riot: httpx2.AsyncClient, riot_id: str) -> EventDetails | None:
    """Fetch one series' details: teams, games, and each game's VOD offsets.

    :param riot: Client from :func:`client`.
    :param riot_id: Riot's series id, as the schedule lists it.
    :return: The series, or None when Riot answers ``event: null`` for an id it no longer serves.
    :raises httpx2.HTTPError: When Riot does not answer with the details.
    :raises pydantic.ValidationError: When the envelope or the series is not readable.
    """
    response = await riot.get("getEventDetails", params={"id": riot_id})
    response.raise_for_status()
    return EventEnvelope.model_validate(response.json()).data.event


def stored_details(details: EventDetails) -> StoredDetails:
    """Reduce Riot's details to the shape we store.

    Only VODs that carry a ``startMillis`` are kept: it is the sole offset the map jump uses, and
    ``endMillis`` is wrong often enough to be unusable. An unknown provider is dropped, not an error.

    :param details: Details from :func:`event_details`.
    :return: The series' tournament id, scores, and per-game offsets.
    """
    if details.match is None:
        return StoredDetails(tournament_id=details.tournament.id)
    games = []
    for game in details.match.games:
        vods = []
        for vod in game.vods:
            try:
                provider = VodProvider(vod.provider)
            except ValueError:
                continue
            if vod.start_millis is None or vod.start_millis < 0:
                continue
            vods.append(
                StoredVod(provider=provider, locale=vod.locale, parameter=vod.parameter, start_ms=vod.start_millis)
            )
        games.append(StoredGame(number=game.number, state=game.state, vods=vods))
    return StoredDetails(
        tournament_id=details.tournament.id,
        game_wins={team.name: team.result.game_wins if team.result else 0 for team in details.match.teams},
        games=games,
    )


async def report_rejection(redis: Redis, error: httpx2.HTTPStatusError) -> None:
    """Alert once an hour that Riot rejected the configured key.

    A rotated key is invisible otherwise: every call fails and the stored data keeps serving.
    Recording the alert is best-effort, so a Redis failure is logged and never reaches a caller:
    a rejected key must not become a failed cron run or an HTTP 500.

    :param redis: Shared Redis client.
    :param error: The failed response.
    :return: None.
    """
    if error.response.status_code not in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return
    try:
        alerted = await redis.set(RIOT_WEB_KEY_ALERTED_KEY, 1, nx=True, ex=RIOT_WEB_KEY_ALERT_INTERVAL)
    except RedisError:
        logger.warning("could not record the Riot key rejection alert", exc_info=True)
        return
    if alerted:
        logger.error(
            "Riot web rejected RIOT_WEB_API_KEY (HTTP %s); rotate it, see docs/cron.md",
            error.response.status_code,
        )


def stops_run(error: httpx2.HTTPStatusError) -> bool:
    """Return whether a failed Riot call should end the run instead of trying the next id.

    :param error: The failed response.
    :return: True for a rejected key or throttling.
    """
    return error.response.status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.TOO_MANY_REQUESTS,
    )


async def standings(riot: httpx2.AsyncClient, tournament_id: str, *, timeout: float | None = None) -> Tournament | None:
    """Fetch one tournament's standings layout.

    ``getStandings`` answers 502 without a tournament id, so the id must already be known; an
    unknown id answers with no standings instead of an error.

    :param riot: Client from :func:`client`.
    :param tournament_id: A validated Riot tournament id.
    :param timeout: Per-request timeout; None uses the client's.
    :return: Riot's layout, or None when it has none. An unreadable entry is logged and skipped.
    :raises httpx2.HTTPError: When Riot does not answer.
    :raises pydantic.ValidationError: When the envelope is not a standings answer.
    """
    params = {"tournamentId": tournament_id}
    if timeout is None:
        response = await riot.get("getStandings", params=params)
    else:
        response = await riot.get("getStandings", params=params, timeout=timeout)
    response.raise_for_status()
    for item in StandingsEnvelope.model_validate(response.json()).data.standings:
        try:
            return Tournament.model_validate(item)
        except ValidationError:
            logger.warning("skipping an unreadable Riot web standings entry", exc_info=True)
    return None


async def refresh_schedule(sessions: Sessions, redis: Redis) -> str | None:
    """Walk Riot's schedule into Postgres, then link each series to the VLR match it is.

    The walk stops at the lookback edge or after ``RIOT_WEB_SCHEDULE_MAX_PAGES``; the cursor is
    ``pageToken``, because Riot ignores ``page`` and answers page one forever. Links are written
    only while a row is unlinked, so no run rewrites an existing link.

    :param sessions: Session factory for the subscription database.
    :param redis: Shared Redis client, for the cached VLR listing and the key-rejection alert.
    :return: A one-line summary of the run, or None when a read or write failed (already logged).
    """
    now = datetime.now(UTC)
    events: list[ScheduleEvent] = []
    async with client() as riot:
        page_token = None
        for _ in range(RIOT_WEB_SCHEDULE_MAX_PAGES):
            try:
                page = await schedule_page(riot, page_token)
            except httpx2.HTTPStatusError as error:
                await report_rejection(redis, error)
                logger.warning("could not walk Riot's web schedule: %s", error)
                return None
            except (httpx2.HTTPError, ValidationError, json.JSONDecodeError) as error:
                logger.warning("could not walk Riot's web schedule: %s", error)
                return None
            events.extend(page.events)
            if not page.older or not page.events:
                break
            if min(event.start_time for event in page.events) < now - RIOT_WEB_SCHEDULE_LOOKBACK:
                break
            page_token = page.older

    try:
        async with sessions.begin() as session:
            rows = await riot_web_store.upsert_matches(session, events)
    except SQLAlchemyError:
        logger.warning("could not store Riot's web schedule", exc_info=True)
        return None

    listing = None
    if raw := await redis.get("matches"):
        try:
            listing = schemas.MatchListAdapter.validate_json(raw)
        except ValueError:
            logger.warning("the cached VLR match listing is unreadable; not linking Riot series this run")
    if not listing:
        return f"{rows} rows, no VLR listing to link against"

    try:
        async with sessions() as session:
            candidates = await riot_web_store.unlinked_between(
                session, now - RIOT_WEB_SCHEDULE_LOOKBACK, now + timedelta(days=14)
            )
            claimed = await riot_web_store.linked_vlr_ids(session)
    except SQLAlchemyError:
        logger.warning("could not read Riot web rows to link", exc_info=True)
        return None

    links = riot_web_link.link(candidates, listing, claimed)
    try:
        async with sessions.begin() as session:
            linked = await riot_web_store.save_links(session, links)
    except SQLAlchemyError:
        logger.warning("could not store Riot web links", exc_info=True)
        return None
    return f"{rows} rows, {linked} new links ({sum(1 for link in links if link.minutes == 0)} exact-time)"


async def refresh_details(sessions: Sessions, redis: Redis) -> str | None:
    """Fetch the details of linked series that are due, and store their per-map VOD offsets.

    Every id is fetched before any write, so no transaction is held across network I/O. The run
    stops fetching on a rejected key or a rate limit, because every later call would fail the same
    way, and still stores what it already fetched.

    :param sessions: Session factory for the subscription database.
    :param redis: Shared Redis client, for the key-rejection alert.
    :return: A one-line summary of the run, or None when there was nothing due or a write failed.
    """
    now = datetime.now(UTC)
    try:
        async with sessions() as session:
            due = await riot_web_store.details_due(session, now, RIOT_WEB_DETAILS_BATCH)
    except SQLAlchemyError:
        logger.warning("could not read the Riot web series whose details are due", exc_info=True)
        return None
    if not due:
        return None

    fetched = []
    async with client() as riot:
        for riot_id in due:
            try:
                details = await event_details(riot, riot_id)
            except httpx2.HTTPStatusError as error:
                await report_rejection(redis, error)
                logger.warning("could not fetch Riot's details for %s: %s", riot_id, error)
                if stops_run(error):
                    break
                continue
            except (httpx2.HTTPError, ValidationError, json.JSONDecodeError) as error:
                logger.warning("could not read Riot's details for %s: %s", riot_id, error)
                continue
            fetched.append((riot_id, stored_details(details) if details else None))

    for riot_id, details in fetched:
        try:
            async with sessions.begin() as session:
                await riot_web_store.save_details(session, riot_id, details, now)
        except SQLAlchemyError:
            logger.warning("could not store Riot's details for %s", riot_id, exc_info=True)
            return None
    return f"{len(due)} due, {len(fetched)} stored"
