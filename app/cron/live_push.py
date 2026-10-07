import asyncio
import json
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from http import HTTPStatus

import httpx2
from firebase_admin import App
from pydantic import ValidationError
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
from app.schemas.matches import CompactState, MatchWithDetails, VideoDelivery, VideoScore
from app.services import fcm, matches, push, scrape_store
from app.services.apns import APNsClient, APNsError
from app.services.push import Routing
from app.services.subscription_store import SubscriptionStore
from app.utils import is_final, is_live

logger = logging.getLogger(__name__)

# APNs reasons meaning the device token will never work again.
_DEAD_TOKEN_REASONS = constants.DEAD_TOKEN_REASONS


async def live_push_cron(ctx: dict) -> None:
    """Send compact updates for matches that are live or have stored push state.

    A match the video tracker is healthily reading, and whose latest score was delivered, is left to its updates.

    :param ctx: arq job context holding the Redis client.
    :return: None.
    """
    get_current_scope().set_transaction_name("Live Push Cron")
    sessions = connections.subscription_sessions
    if not settings.ENABLE_LIVE_PUSH or sessions is None:
        return

    client = ctx["redis"]
    video = await push.video_score(client)
    left_to_video = await _match_left_to_video(client, video)
    live_ids = await _listed_live_ids(client)
    async with sessions() as session:
        stored = set(await SubscriptionStore(session).list_match_ids())
    match_ids = sorted(((live_ids or set()) | stored) - {left_to_video})
    details = await asyncio.gather(*(_fetch_detail(client, match_id) for match_id in match_ids), return_exceptions=True)
    video = await push.video_score(client)  # the tracker may have written a newer score during the fetches
    fcm_app = fcm.get_app() if settings.GOOGLE_APPLICATION_CREDENTIALS else None
    await _cache_details(client, match_ids, details, left_to_video)
    video_match = await push.resolve_video_match(client, video) if video is not None else None

    for match_id, detail in zip(match_ids, details, strict=True):
        failures_key = constants.PUSH_FETCH_FAILURES_KEY.format(match_id)
        delivered = False
        represented = False
        try:
            async with sessions() as session:
                store = SubscriptionStore(session)
                if isinstance(detail, BaseException):
                    if not isinstance(detail, ScrapingError):
                        # A parser bug or an unreachable VLR says nothing about the match, so neither
                        # counts toward ending it; only VLR's own fetch failures do.
                        if isinstance(detail, httpx2.TransportError):
                            logger.warning("could not read match %s: VLR unreachable", match_id, exc_info=detail)
                        else:
                            logger.exception("could not read match %s", match_id, exc_info=detail)
                    elif live_ids is None:
                        # Without a listing, a missing live ID says nothing about whether the match is gone.
                        logger.warning(
                            "could not read match %s while the live listing is unavailable", match_id, exc_info=detail
                        )
                    elif match_id in live_ids:
                        # VLR's listing still shows the match as live, so a failed detail fetch is a hiccup.
                        logger.warning("could not read match %s while it is listed live", match_id, exc_info=detail)
                    else:
                        status = detail.upstream_status
                        if status == HTTPStatus.NOT_FOUND or (
                            status >= HTTPStatus.INTERNAL_SERVER_ERROR
                            and await _fetch_failure_limit_reached(client, failures_key)
                        ):
                            logger.warning("ending match %s: VLR page unavailable (%r)", match_id, detail)
                            await _end_unavailable_match(store, session, fcm_app, match_id)
                        elif status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                            # The failure was counted; keep the streak and retry on the next run.
                            raise detail
                        else:
                            # A rate limit or a rejection says VLR is up, so it never counts toward
                            # ending the match; the fetch may succeed on the next run.
                            logger.warning("could not read match %s: VLR returned %s", match_id, status)
                else:
                    scores = video_match[2] if video_match is not None and video_match[0] == match_id else None
                    if video is not None and scores is not None:
                        push.raise_map_scores(detail, video.map_number, scores)
                        push.order_teams_for_broadcast(detail, video)
                        # Without a stats panel the projection may still show the previous map.
                        represented = push.represents_video(detail, video, scores)
                    delivered = await _push_match(
                        store,
                        session,
                        fcm_app,
                        match_id,
                        detail,
                        listed_live=live_ids is not None and match_id in live_ids,
                        # Apply video pause only to the resolved match; game numbers are not unique across matches.
                        video=video if scores is not None else None,
                    )
                await session.commit()
            # The match was read, ended, or the failure didn't count, so consecutive failures restart.
            await client.delete(failures_key)
            if delivered and represented and video is not None:
                await _mark_delivered(client, match_id, video)
        except Exception:
            logger.warning("live push failed for match %s", match_id, exc_info=True)
    await _store_teams(sessions, match_ids, details)


async def push_video_match() -> None:
    """Push the video tracker's match from its latest score and the match's last fetched details.

    Nothing is fetched from VLR, so a slow or failing VLR never delays the tracker's update. Before the cron has
    fetched the match there are no details, and the cron pushes it instead.

    :return: None.
    """
    get_current_scope().set_transaction_name("Live Push Video")
    sessions = connections.subscription_sessions
    if not settings.ENABLE_LIVE_PUSH or sessions is None:
        return
    client = cache.get_client()
    try:
        if (video := await push.video_score(client)) is None:
            return
        await _deliver_video_match(sessions, client, video, refresh=False)
    except Exception:
        logger.warning("video push failed", exc_info=True)
    finally:
        await client.aclose()


async def refresh_video_match() -> None:
    """Re-send unchanged live state to Android followers on tracker heartbeats.

    :return: None.
    """
    get_current_scope().set_transaction_name("Live Push Refresh")
    sessions = connections.subscription_sessions
    if not settings.ENABLE_LIVE_PUSH or sessions is None:
        return
    client = cache.get_client()
    try:
        if (video := await push.video_score(client)) is None or not video.healthy:
            return
        await _deliver_video_match(sessions, client, video, refresh=True)
    except Exception:
        logger.warning("video refresh failed", exc_info=True)
    finally:
        await client.aclose()


async def _deliver_video_match(
    sessions: async_sessionmaker[AsyncSession], client: Redis, video: VideoScore, *, refresh: bool
) -> None:
    """Deliver the match the tracker resolves to, immediately or as a rate-limited refresh.

    :param sessions: Database session factory.
    :param client: Redis client.
    :param video: Latest tracker score.
    :param refresh: Whether this re-sends unchanged state, FCM-only and rate-limited.
    :return: None.
    """
    if (resolved := await push.resolve_video_match(client, video)) is None:
        return
    match_id, detail, scores = resolved
    if refresh:
        # Rate-limit unchanged re-sends to at most once per refresh window.
        if not await client.set(
            constants.PUSH_REFRESH_KEY.format(match_id), 1, nx=True, ex=constants.PUSH_REFRESH_SECONDS
        ):
            return
    else:
        # Reset the refresh window since this push carries the latest state.
        await client.set(constants.PUSH_REFRESH_KEY.format(match_id), 1, ex=constants.PUSH_REFRESH_SECONDS)
    if not any(scores.values()):
        return  # at a map's 0-0 VLR still shows the last map, so the cron pushes until a round is won
    push.raise_map_scores(detail, video.map_number, scores)
    push.order_teams_for_broadcast(detail, video)
    fcm_app = fcm.get_app() if settings.GOOGLE_APPLICATION_CREDENTIALS else None
    if refresh:
        state = push.project_state(match_id, detail, video)
        if state is None or state.terminal:
            return
        async with sessions.begin() as session:
            tokens = await SubscriptionStore(session).live_android_tokens(push.routing_ids(match_id, detail))
        # The token read is committed before the send; publish_direct clears dead tokens in its own session.
        # Skip when a newer score was stored: its own push carries the change, and this refresh is stamped later.
        if (latest := await push.video_score(client)) is None or not video.same_score(latest):
            return
        await _send_fcm(tokens, fcm_app, state, refresh=True)
        return
    async with sessions() as session:
        delivered = await _push_match(
            SubscriptionStore(session), session, fcm_app, match_id, detail, listed_live=True, video=video
        )
        await session.commit()
    if delivered and push.represents_video(detail, video, scores):
        await _mark_delivered(client, match_id, video)


async def _cache_details(
    client: Redis, match_ids: list[str], details: list[MatchWithDetails | BaseException], left_to_video: str | None
) -> None:
    """Keep each tracked match's details, which the video push uses instead of fetching VLR.

    A failed fetch keeps the previous details, so a VLR outage doesn't silence the video. Finished matches and
    matches this run no longer tracks are dropped, so the video can't be matched to them.

    :param client: Redis client.
    :param match_ids: Match IDs this run tracks.
    :param details: Each match's details, or the error fetching it.
    :param left_to_video: The match the video pushes, which this run didn't fetch but still tracks.
    :return: None.
    """
    cached = await push.cached_details(client)
    kept = {left_to_video: cached[left_to_video]} if left_to_video in cached else {}
    for match_id, detail in zip(match_ids, details, strict=True):
        if match_id == constants.TEST_MATCH_ID:
            continue
        if isinstance(detail, BaseException):
            if match_id in cached:
                kept[match_id] = cached[match_id]
        elif not is_final(detail.event.status):
            kept[match_id] = detail.model_dump(mode="json")
    await client.set(constants.PUSH_DETAILS_KEY, json.dumps(kept), ex=constants.PUSH_DETAILS_TTL)


async def _mark_delivered(client: Redis, match_id: str, video: VideoScore) -> None:
    """Record that phones got this video score for the match, so the cron can leave the match to the video.

    :param client: Redis client.
    :param match_id: Match the score was pushed for.
    :param video: The score pushed.
    :return: None.
    """
    await client.set(
        constants.VIDEO_DELIVERED_KEY,
        VideoDelivery(match_id=match_id, video=video).model_dump_json(),
        ex=constants.VIDEO_SCORE_TTL,
    )


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


async def _match_left_to_video(client: Redis, video: VideoScore | None) -> str | None:
    """Find the match the video tracker pushes on its own, which the minute cron then leaves alone.

    That holds while the tracker reports a healthy read and its latest score has been delivered; after an error,
    silence, or a failed delivery, the cron pushes the match from VLR again.

    :param client: Redis client.
    :param video: The tracker's latest score.
    :return: The match ID, or None when VLR should push every match.
    """
    if video is None or not video.healthy or not (data := await client.get(constants.VIDEO_DELIVERED_KEY)):
        return None
    try:
        delivered = VideoDelivery.model_validate_json(data)
    except ValidationError:
        return None
    return delivered.match_id if delivered.video.same_score(video) else None


async def _listed_live_ids(client: Redis) -> set[str] | None:
    """Return the match IDs the VLR listing shows as live, plus a running test match.

    The listing is fetched from VLR only while the cached match list (refreshed every five minutes) has a match
    that is live or starts within ``PUSH_LISTING_LEAD``, so idle minutes cost no VLR request.

    :param client: Redis client.
    :return: Live match IDs, or None when the listing cannot be fetched or parsed.
    """
    try:
        listed = await matches.get_upcoming_matches(redis_client=client) if await _match_due(client) else []
    except Exception:
        logger.warning("could not list live matches", exc_info=True)
        return None
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
    video: VideoScore | None = None,
) -> bool:
    """Deliver one match's state, finishing it when the match is final.

    :param store: Subscription store.
    :param session: Match-scoped database session.
    :param fcm_app: Firebase app, or None when FCM is not configured.
    :param match_id: Match identifier.
    :param detail: Scraped match details.
    :param listed_live: Whether the listing currently shows the match as live.
    :param video: Tracker score for this match, or None.
    :return: Whether every provider send succeeded; True when there was nothing to send.
    :raises SQLAlchemyError: If a database operation fails.
    """
    state = push.project_state(match_id, detail, video)
    if state is None:
        return True
    row = await store.get_match(match_id)
    if row is None and listed_live:
        # Keep a row so the match gets a final pass after it leaves the live listing.
        await store.save_match(match_id, None, None)
        await session.commit()  # release the SQLite write lock before any provider call
    routing = push.routing_ids(match_id, detail)
    tokens = await store.live_android_tokens(routing) if fcm_app is not None else []
    await session.commit()  # finish the token read before the provider send

    if state.terminal:
        unstarted = _unstarted(state)
        if unstarted:
            # A cancelled or walkover match has no play to report, so a broadcast would be a fake "FINAL 0-0".
            logger.warning("ending match %s without a final push: its state has no play", match_id)
            sent = True
        else:
            sent = await _send_fcm(tokens, fcm_app, state)
        if row is not None and row.channel_id:
            # A card for a match with no play is dismissed now instead of after the usual delay.
            await _end_apns(row.channel_id, state, immediate_dismissal=unstarted)
        await store.delete_match(match_id)
        return sent

    if not (listed_live or is_live(detail.event.status)):
        return True
    sent = await _send_fcm(tokens, fcm_app, state)
    if connections.apns_client is not None:
        sent = await _push_apns(store, session, connections.apns_client, state, routing, row) and sent
    return sent


async def _fetch_failure_limit_reached(client: Redis, failures_key: str) -> bool:
    """Count one more consecutive failed fetch for a match.

    :param client: Redis client.
    :param failures_key: The match's failure-counter key.
    :return: Whether the match has now failed the limit of consecutive runs.
    """
    failures = await client.incr(failures_key)
    await client.expire(failures_key, constants.PUSH_FETCH_FAILURES_TTL)
    return failures >= constants.PUSH_FETCH_FAILURE_LIMIT


def _unstarted(state: CompactState) -> bool:
    """Check whether a compact state shows no play at all.

    :param state: Compact score state.
    :return: True when no map is current or won and the series is scoreless.
    """
    return state.current_map is None and not any(state.map_winners) and all(not team.score for team in state.teams)


async def _end_unavailable_match(
    store: SubscriptionStore, session: AsyncSession, fcm_app: App | None, match_id: str
) -> None:
    """End a stored match whose VLR page is gone or keeps failing, using its last sent score.

    :param store: Subscription store.
    :param session: Match-scoped database session.
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
        if _unstarted(state):
            # Terminal or not, a scoreless state would announce a fake "FINAL 0-0" for a match that never played.
            logger.warning("dropping match %s without a final push: its last state has no play", match_id)
            channel_id = row.channel_id
            await store.delete_match(match_id)
            await session.commit()  # release the SQLite write lock before the APNs call
            if channel_id:
                # End the activity now, so the scoreless pre-match card does not linger on the screen.
                await _end_apns(channel_id, state, immediate_dismissal=True)
            return
        # Without the match page, event and player IDs are unknown; the stored teams still carry theirs.
        team_ids = [team.id for team in state.teams if team.id]
        routing = Routing(match_id, None, team_ids, [])
        tokens = await store.live_android_tokens(routing) if fcm_app is not None else []
        await session.commit()  # finish the token read before the provider send
        await _send_fcm(tokens, fcm_app, state)
        if row.channel_id:
            await _end_apns(row.channel_id, state)
    await store.delete_match(match_id)


async def _send_fcm(tokens: list[str], fcm_app: App | None, state: CompactState, *, refresh: bool = False) -> bool:
    """Send match state directly to active Android follower FCM tokens.

    The caller has already read the tokens, so no database transaction is open across the send.

    :param tokens: Follower FCM registration tokens.
    :param fcm_app: Firebase app, or None when FCM is not configured.
    :param state: Compact score state.
    :param refresh: Whether this re-sends unchanged state on a tracker heartbeat.
    :return: False if the send failed; True when it succeeded or there was nothing to send.
    """
    if fcm_app is None or not tokens:
        return True
    try:
        message_ids = await fcm.publish_direct(fcm_app, tokens, state)
    except Exception:
        logger.warning("FCM %s failed for match %s", "refresh" if refresh else "update", state.match_id, exc_info=True)
        return False
    current = state.current_map
    # One line per send, so a stale or missing phone notification can be traced to a send or its absence.
    logger.info(
        "FCM %s match %s map %s %s series %s terminal=%s pause=%s direct=%d: %s",
        "refreshed" if refresh else "sent",
        state.match_id,
        current.number if current else None,
        "-".join(map(str, current.scores)) if current else None,
        "-".join(str(team.score) for team in state.teams),
        state.terminal,
        state.pause.kind if state.pause else None,
        len(tokens),
        message_ids,
    )
    return True


async def _end_apns(channel_id: str, state: CompactState, *, immediate_dismissal: bool = False) -> None:
    """Send the final state to the APNs channel and delete it, logging any failure.

    :param channel_id: APNs broadcast channel ID.
    :param state: Final compact score state.
    :param immediate_dismissal: Whether to dismiss the ended activity now instead of after the usual delay.
    :return: None.
    """
    apns = connections.apns_client
    if apns is None:
        return
    try:
        await apns.publish(channel_id, state, terminal=True, immediate_dismissal=immediate_dismissal)
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
) -> bool:
    """Create the match channel when needed, start new activities, and broadcast score changes.

    :param store: Subscription store.
    :param session: Match-scoped database session.
    :param apns: APNs client.
    :param state: Compact score state.
    :param routing: Match routing IDs.
    :param row: Stored push state, or None for a new match.
    :return: False if the channel creation or broadcast failed; failed starts are per device and don't count.
    :raises SQLAlchemyError: If a database operation fails.
    """
    starts = await store.pending_starts(routing)
    await session.commit()  # finish the start query before the provider calls
    channel_id = row.channel_id if row is not None else None
    last_state = row.last_state_json if row is not None else None
    if starts and channel_id is None:
        try:
            channel_id = await apns.create_channel()
        except Exception:
            logger.warning("APNs channel creation failed for match %s", state.match_id, exc_info=True)
            return False
        # The start payload carries the current state, so it counts as sent.
        last_state = state.semantic()
        await store.save_match(state.match_id, channel_id, last_state)
        await session.commit()  # release the SQLite write lock before the provider calls
    if channel_id is None:
        return True

    for client_id, token in starts:
        await _start_activity(store, session, apns, client_id, token, channel_id, state)

    # A start that was already recorded leaves its read transaction open, and the broadcast must not hold one.
    await session.commit()
    if state.semantic() != last_state:
        try:
            await apns.publish(channel_id, state, terminal=False)
            await store.save_match(state.match_id, channel_id, state.semantic())
        except Exception:
            logger.warning("APNs update failed for match %s", state.match_id, exc_info=True)
            return False
    return True


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
