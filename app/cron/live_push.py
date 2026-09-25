import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from http import HTTPStatus

import httpx2
from firebase_admin import App
from redis.asyncio import Redis
from sentry_sdk import get_current_scope
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import constants, schemas
from app.cache import cache
from app.core import connections
from app.core.config import settings
from app.cron import synthetic_match
from app.db.models import MatchPushState
from app.exceptions import ScrapingError
from app.schemas.matches import CompactState, MatchWithDetails
from app.services import fcm, matches, push, scrape_store
from app.services.apns import APNsClient, APNsError
from app.services.push import Routing
from app.services.subscription_store import SubscriptionStore
from app.utils import is_live

logger = logging.getLogger(__name__)

# APNs reasons meaning the device token will never work again.
_DEAD_TOKEN_REASONS = constants.DEAD_TOKEN_REASONS


async def live_push_cron(ctx: dict) -> None:
    """Send compact updates for matches that are live or have stored push state.

    :param ctx: arq job context holding the Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Live Push Cron")
    sessions = connections.subscription_sessions
    if not settings.ENABLE_LIVE_PUSH or sessions is None:
        return

    client = ctx["redis"]
    live_ids = await _listed_live_ids(client)
    async with sessions() as session:
        match_ids = sorted(live_ids | set(await SubscriptionStore(session).list_match_ids()))
    details = await asyncio.gather(*(_fetch_detail(client, match_id) for match_id in match_ids), return_exceptions=True)
    fcm_app = fcm.get_app() if settings.GOOGLE_APPLICATION_CREDENTIALS else None

    for match_id, detail in zip(match_ids, details, strict=True):
        failures_key = constants.PUSH_FETCH_FAILURES_KEY.format(match_id)
        try:
            async with sessions() as session:
                store = SubscriptionStore(session)
                if isinstance(detail, BaseException):
                    gone = isinstance(detail, ScrapingError) and detail.upstream_status == HTTPStatus.NOT_FOUND
                    # An unreachable VLR says nothing about the match, so it never counts toward ending it.
                    if isinstance(detail, httpx2.TransportError) or not (
                        gone or await _fetch_failure_limit_reached(client, failures_key)
                    ):
                        raise detail
                    logger.warning("ending match %s: VLR page unavailable (%r)", match_id, detail)
                    await _end_unavailable_match(store, fcm_app, match_id)
                else:
                    await _push_match(store, session, fcm_app, match_id, detail, listed_live=match_id in live_ids)
                await session.commit()
            await client.delete(failures_key)
        except Exception:
            logger.warning("live push failed for match %s", match_id, exc_info=True)
    await _store_teams(sessions, match_ids, details)


async def _store_teams(
    sessions: async_sessionmaker[AsyncSession], match_ids: list[str], details: list[MatchWithDetails | BaseException]
) -> None:
    """Store the teams of each fetched match, whose pages carry the tag.

    :param sessions: Database session factory.
    :param match_ids: Fetched match IDs.
    :param details: Each match's details, or the error fetching it.
    :return: None.
    """
    try:
        async with sessions.begin() as session:
            for match_id, detail in zip(match_ids, details, strict=True):
                if match_id == constants.TEST_MATCH_ID or isinstance(detail, BaseException):
                    continue
                for team in detail.teams:
                    if team.id:
                        await scrape_store.upsert_team(
                            session, team.id, name=team.name, tag=team.tag, logo=str(team.img)
                        )
    except SQLAlchemyError:
        # The store never fails the cron; pushes have already been sent.
        logger.warning("could not store match teams", exc_info=True)


async def _listed_live_ids(client: Redis) -> set[str]:
    """Return the match IDs the VLR listing shows as live, plus a running test match.

    The listing is fetched from VLR only while the cached match list (refreshed every five minutes) has a match
    that is live or starts within ``PUSH_LISTING_LEAD``, so idle minutes cost no VLR request.

    :param client: Redis client.
    :return: Live match IDs; empty when the listing cannot be fetched or no match is due.
    """
    listed = []
    try:
        if await _match_due(client):
            listed = await matches.get_upcoming_matches(redis_client=client)
    except Exception:
        logger.warning("could not list live matches", exc_info=True)
    live_ids = {match.id for match in listed if is_live(match.status)}
    if await client.exists(constants.TEST_TICK_KEY):
        live_ids.add(constants.TEST_MATCH_ID)
    return live_ids


async def _match_due(client: Redis) -> bool:
    """Check whether the cached match list has a match that is live or about to start.

    :param client: Redis client.
    :return: Whether the live listing is worth fetching; ``True`` when the cache is empty.
    """
    if not (data := await cache.get("matches", client=client)):
        return True
    horizon = datetime.now(UTC) + constants.PUSH_LISTING_LEAD
    return any(
        is_live(match.status) or (match.status != constants.MatchStatus.COMPLETED and match.time <= horizon)
        for match in schemas.MatchListAdapter.validate_json(data)
    )


def _fetch_detail(client: Redis, match_id: str) -> Awaitable[MatchWithDetails]:
    """Fetch one match, or the next synthetic observation for the test match.

    :param client: Redis client.
    :param match_id: Match identifier.
    :return: Awaitable resolving to the match details.
    """
    if match_id == constants.TEST_MATCH_ID:
        return synthetic_match.next_observation(client)
    return matches.match_by_id(match_id, redis_client=client)


async def _push_match(
    store: SubscriptionStore,
    session: AsyncSession,
    fcm_app: App | None,
    match_id: str,
    detail: MatchWithDetails,
    *,
    listed_live: bool,
) -> None:
    """Deliver one match's state, finishing it when the match is final.

    :param store: Subscription store.
    :param session: Match-scoped database session.
    :param fcm_app: Firebase app, or None when FCM is not configured.
    :param match_id: Match identifier.
    :param detail: Scraped match details.
    :param listed_live: Whether the listing currently shows the match as live.
    :return: None.
    :raises SQLAlchemyError: If a database operation fails.
    """
    state = push.project_state(match_id, detail)
    if state is None:
        return
    row = await store.get_match(match_id)
    if row is None and listed_live:
        # Keep a row so the match gets a final pass after it leaves the live listing.
        await store.save_match(match_id, None, None)
        await session.commit()  # release the SQLite write lock before any provider call
    routing = push.routing_ids(match_id, detail)

    if state.terminal:
        await _send_fcm(store, fcm_app, state, routing)
        if row is not None and row.channel_id:
            await _end_apns(row.channel_id, state)
        await store.delete_match(match_id)
        return

    if not (listed_live or is_live(detail.event.status)):
        return
    await _send_fcm(store, fcm_app, state, routing)
    if connections.apns_client is not None:
        await _push_apns(store, session, connections.apns_client, state, routing, row)


async def _fetch_failure_limit_reached(client: Redis, failures_key: str) -> bool:
    """Count one more consecutive failed fetch for a match.

    :param client: Redis client.
    :param failures_key: The match's failure-counter key.
    :return: Whether the match has now failed the limit of consecutive runs.
    """
    failures = await client.incr(failures_key)
    await client.expire(failures_key, constants.PUSH_FETCH_FAILURES_TTL)
    return failures >= constants.PUSH_FETCH_FAILURE_LIMIT


async def _end_unavailable_match(store: SubscriptionStore, fcm_app: App | None, match_id: str) -> None:
    """End a stored match whose VLR page is gone or keeps failing, using its last sent score.

    :param store: Subscription store.
    :param fcm_app: Firebase app, or None when FCM is not configured.
    :param match_id: Match identifier.
    :return: None.
    :raises SQLAlchemyError: If a database operation fails.
    """
    row = await store.get_match(match_id)
    if row is None:
        return
    # Only matches with an APNs channel have a stored score; Android-only rows are just removed.
    if row.last_state_json is not None:
        state = push.final_from_last_sent(row.last_state_json)
        # Without the match page, event, team, and player IDs are unknown, so only the match topic gets this.
        await _send_fcm(store, fcm_app, state, Routing(match_id, None, [], []))
        if row.channel_id:
            await _end_apns(row.channel_id, state)
    await store.delete_match(match_id)


async def _send_fcm(store: SubscriptionStore, fcm_app: App | None, state: CompactState, routing: Routing) -> None:
    """Send the state to the match's FCM topics, logging any failure.

    :param store: Subscription store.
    :param fcm_app: Firebase app, or None when FCM is not configured.
    :param state: Compact score state.
    :param routing: Match routing IDs.
    :return: None.
    """
    if fcm_app is None:
        return
    try:
        player_ids = await store.active_player_ids(routing.player_ids)
        message_ids = await fcm.publish(fcm_app, fcm.build_messages(state, routing, player_ids))
        current = state.current_map
        # One line per send, so a stale or missing phone notification can be traced to a send or its absence.
        logger.info(
            "FCM sent match %s map %s %s series %s terminal=%s: %s",
            state.match_id,
            current.number if current else None,
            "-".join(map(str, current.scores)) if current else None,
            "-".join(str(team.score) for team in state.teams),
            state.terminal,
            message_ids,
        )
    except Exception:
        logger.warning("FCM update failed for match %s", state.match_id, exc_info=True)


async def _end_apns(channel_id: str, state: CompactState) -> None:
    """Send the final state to the APNs channel and delete it, logging any failure.

    :param channel_id: APNs broadcast channel ID.
    :param state: Final compact score state.
    :return: None.
    """
    apns = connections.apns_client
    if apns is None:
        return
    try:
        await apns.publish(channel_id, state, terminal=True)
        await apns.delete_channel(channel_id)
    except Exception:
        logger.warning("APNs final update failed for match %s", state.match_id, exc_info=True)


async def _push_apns(
    store: SubscriptionStore,
    session: AsyncSession,
    apns: APNsClient,
    state: CompactState,
    routing: Routing,
    row: MatchPushState | None,
) -> None:
    """Create the match channel when needed, start new activities, and broadcast score changes.

    :param store: Subscription store.
    :param session: Match-scoped database session.
    :param apns: APNs client.
    :param state: Compact score state.
    :param routing: Match routing IDs.
    :param row: Stored push state, or None for a new match.
    :return: None.
    :raises SQLAlchemyError: If a database operation fails.
    """
    starts = await store.pending_starts(routing)
    channel_id = row.channel_id if row is not None else None
    last_state = row.last_state_json if row is not None else None
    if starts and channel_id is None:
        try:
            channel_id = await apns.create_channel()
        except Exception:
            logger.warning("APNs channel creation failed for match %s", state.match_id, exc_info=True)
            return
        # The start payload carries the current state, so it counts as sent.
        last_state = state.semantic()
        await store.save_match(state.match_id, channel_id, last_state)
    if channel_id is None:
        return

    for client_id, token in starts:
        await _start_activity(store, session, apns, client_id, token, channel_id, state)

    if state.semantic() != last_state:
        try:
            await apns.publish(channel_id, state, terminal=False)
            await store.save_match(state.match_id, channel_id, state.semantic())
        except Exception:
            logger.warning("APNs update failed for match %s", state.match_id, exc_info=True)


async def _start_activity(
    store: SubscriptionStore,
    session: AsyncSession,
    apns: APNsClient,
    client_id: str,
    token: str,
    channel_id: str,
    state: CompactState,
) -> None:
    """Send one push-to-start at most once per client and match.

    :param store: Subscription store.
    :param session: Match-scoped database session.
    :param apns: APNs client.
    :param client_id: Client UUID.
    :param token: APNs push-to-start token.
    :param channel_id: APNs broadcast channel the activity listens on.
    :param state: Compact score state.
    :return: None.
    :raises SQLAlchemyError: If a database operation fails.
    """
    if not await store.mark_started(client_id, state.match_id):
        return
    await session.commit()
    try:
        await apns.send_start(token, channel_id, state)
    except APNsError as exc:
        if exc.reason in _DEAD_TOKEN_REASONS:
            await store.clear_token(token)
            await session.commit()  # release the write lock before the next provider call
        logger.warning("APNs start failed for match %s: %s", state.match_id, exc.reason)
