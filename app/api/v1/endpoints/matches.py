import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app import cache, constants, schemas
from app.api import deps
from app.core.config import settings
from app.exceptions import BadRequestError, ServiceUnavailableError
from app.services import live, matches

router = APIRouter()
logger = logging.getLogger(__name__)

_DECIMAL_ID = re.compile(r"[0-9]{1,10}")


class LiveSubscription(NamedTuple):
    """Store the data for one accepted live stream.

    :param client: The Redis client for the stream.
    :param stream_id: The unique lease ID for the stream.
    :param match_ids: The match IDs that the stream watches.
    """

    client: Redis
    stream_id: str
    match_ids: list[str]


async def _live_subscription(
    client: Annotated[Redis, Depends(deps.get_redis_client)],
    match_id: Annotated[list[str] | None, Query(description="Match id to watch; repeat for multiple matches.")] = None,
) -> AsyncIterator[LiveSubscription]:
    """Register and release the Redis leases for one live stream.

    FastAPI registers the leases before it starts the SSE response. It releases
    the leases after the stream stops.

    :param client: The Redis client for live state.
    :param match_id: The match IDs from the query string.
    :return: An asynchronous iterator that yields one accepted subscription.
    :raises BadRequestError: If the request has invalid match IDs.
    :raises ServiceUnavailableError: If live mode is off or Redis is unavailable.
    """
    if not settings.ENABLE_LIVE_MATCHES:
        raise ServiceUnavailableError("Live match streaming is not enabled")

    raw_ids = [value.strip() for value in (match_id or []) if value.strip()]
    if not raw_ids:
        raise BadRequestError("At least one match_id is required")
    if any(not _DECIMAL_ID.fullmatch(value) for value in raw_ids):
        raise BadRequestError("match_id must be 1 to 10 ASCII digits")
    # Canonical form drops leading zeros, so "00123" and "123" share one lease.
    match_ids = list(dict.fromkeys(str(int(value)) for value in raw_ids))
    if len(match_ids) > constants.LIVE_MAX_MATCHES:
        raise BadRequestError(f"At most {constants.LIVE_MAX_MATCHES} match ids may be watched")

    try:
        stream_id = live.new_stream_id()
        await live.register(client, stream_id, match_ids)
    except RedisError:
        logger.warning("live subscription admission failed", exc_info=True)
        raise ServiceUnavailableError("Live match state is unavailable")

    subscription = LiveSubscription(client=client, stream_id=stream_id, match_ids=match_ids)
    try:
        yield subscription
    finally:
        try:
            await live.release(client, stream_id, match_ids)
        except RedisError:
            logger.warning("live stream lease release failed", exc_info=True)


@router.get("/")
async def get_matches(client: Redis = Depends(deps.get_redis_client)) -> list[schemas.Match]:
    if data := await cache.get("matches", client=client):
        return schemas.MatchListAdapter.validate_json(data)
    return await matches.match_list(redis_client=client)


@router.get("/live", response_class=EventSourceResponse)
async def stream_live_matches(
    subscription: Annotated[LiveSubscription, Depends(_live_subscription)],
) -> AsyncIterator[ServerSentEvent]:
    """Send compact match updates to one SSE client.

    The cron job writes the snapshots. This function only reads Redis. After a
    match ends, the stream stops reading and releases the match lease. The
    stream closes when every watched match has ended.

    :param subscription: The accepted subscription for this stream.
    :return: An asynchronous iterator of SSE events.
    """
    last_version: dict[str, int] = {}
    active_match_ids = list(subscription.match_ids)
    try:
        while active_match_ids:
            snapshots = await live.read_snapshots(subscription.client, active_match_ids)
            for match_id, snapshot in snapshots.items():
                if snapshot is None or snapshot.version == last_version.get(match_id):
                    continue
                last_version[match_id] = snapshot.version
                event = live.project_live_event(snapshot)
                event_name = "end" if event.terminal else "snapshot"
                yield ServerSentEvent(
                    event=event_name,
                    data=event.model_dump(mode="json"),
                    id=f"{match_id}:{snapshot.version}",
                )
                if event.terminal:
                    active_match_ids.remove(match_id)
                    await live.release(subscription.client, subscription.stream_id, [match_id])
            if active_match_ids:
                await live.renew(subscription.client, subscription.stream_id, active_match_ids)
                await asyncio.sleep(constants.LIVE_POLL_INTERVAL)
    except RedisError:
        # Stop the stream if Redis fails. A Redis failure is not an END event.
        # The client can reconnect for the latest state.
        logger.warning("live stream terminated by Redis failure", exc_info=True)


@router.get("/{id}")
async def get_match_by_id(id: str, client: Redis = Depends(deps.get_redis_client)) -> schemas.MatchWithDetails:
    return await matches.match_by_id(id, client)
