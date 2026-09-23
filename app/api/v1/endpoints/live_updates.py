"""Minimal client token and favorites endpoints for live updates."""

import json
import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis

from app import constants
from app.api import deps
from app.constants import Platform
from app.core import connections
from app.cron import synthetic_match
from app.exceptions import BadRequestError, ConflictError, NotFoundError, ScrapingError, ServiceUnavailableError
from app.schemas.matches import CompactState, Favorites, MatchWithDetails, TokenRegistration
from app.services import matches
from app.services.apns import APNsError
from app.services.push import project_state
from app.services.subscription_store import SubscriptionStore
from app.utils import is_live

logger = logging.getLogger(__name__)

router = APIRouter(
    dependencies=[Depends(deps.verify_token), Depends(deps.require_live_push), Depends(deps.set_no_store)]
)


@router.post("/test-match", status_code=status.HTTP_204_NO_CONTENT)
async def trigger_test_match(client: deps.RedisDep) -> None:
    """Start the synthetic test match; returns 409 while one is already running."""
    if not await client.set(constants.TEST_TICK_KEY, 0, nx=True):
        raise ConflictError("Test match is already running")


@router.put("/clients/{client_id}/token", status_code=status.HTTP_204_NO_CONTENT)
async def put_token(client_id: UUID, body: TokenRegistration, store: deps.SubscriptionStoreDep) -> None:
    """Store a client's APNs push-to-start or FCM token."""
    await store.register_token(str(client_id), body.token, body.platform)


@router.delete("/clients/{client_id}/token", status_code=status.HTTP_204_NO_CONTENT)
async def delete_token(client_id: UUID, store: deps.SubscriptionStoreDep) -> None:
    """Delete a client's push token."""
    await store.delete_token(str(client_id))


@router.put("/clients/{client_id}/favorites", status_code=status.HTTP_204_NO_CONTENT)
async def put_favorites(client_id: UUID, body: Favorites, store: deps.SubscriptionStoreDep) -> None:
    """Replace a client's favorites."""
    await store.replace_favorites(str(client_id), body)


@router.get("/clients/{client_id}/favorites")
async def get_favorites(client_id: UUID, store: deps.SubscriptionStoreDep) -> Favorites:
    """Read a client's favorites."""
    return await store.get_favorites(str(client_id))


@router.post(
    "/clients/{client_id}/matches/{match_id}/live-activity",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def start_live_activity(
    client_id: UUID,
    match_id: deps.MatchId,
    store: deps.SubscriptionStoreDep,
    redis_client: deps.RedisDep,
) -> None:
    """Instantly trigger an APNs push-to-start for a live match."""
    token_row = await store.get_token(str(client_id))
    if token_row is None:
        raise NotFoundError("Client token not registered")
    if token_row.platform != Platform.IOS:
        raise BadRequestError("Live activities are only supported on iOS")

    state, channel_id = await _resolve_live_state(match_id, store, redis_client)
    if state.terminal:
        raise BadRequestError("Match has already ended")

    apns = connections.apns_client
    if apns is None:
        raise ServiceUnavailableError("APNs client is not configured")

    if channel_id is None:
        try:
            channel_id = await apns.create_channel()
        except APNsError as exc:
            logger.warning("APNs channel creation failed for match %s: %s", match_id, exc.reason)
            raise ServiceUnavailableError(f"APNs channel creation failed: {exc.reason}") from exc
        await store.save_match(match_id, channel_id, state.semantic())

    await store.mark_started(str(client_id), match_id)

    try:
        await apns.send_start(token_row.token, channel_id, state)
    except APNsError as exc:
        if exc.reason in constants.DEAD_TOKEN_REASONS:
            await store.clear_token(token_row.token)
        logger.warning("APNs start failed for client %s, match %s: %s", client_id, match_id, exc.reason)
        raise ServiceUnavailableError(f"APNs start failed: {exc.reason}") from exc


async def _resolve_live_state(
    match_id: str,
    store: SubscriptionStore,
    redis_client: Redis,
) -> tuple[CompactState, str | None]:
    """Retrieve existing push state for a match, or fetch and validate its live details."""
    if row := await store.get_match(match_id):
        if row.last_state_json:
            state = CompactState.model_validate({**json.loads(row.last_state_json), "observed_at": int(time.time())})
            return state, row.channel_id
        channel_id = row.channel_id
    else:
        channel_id = None

    detail = await _fetch_match_detail(match_id, redis_client)
    if not is_live(detail.event.status):
        raise BadRequestError("Match is not live")
    if (state := project_state(match_id, detail)) is None:
        raise BadRequestError("Match data is incomplete")
    return state, channel_id


async def _fetch_match_detail(match_id: str, redis_client: Redis) -> MatchWithDetails:
    """Fetch match details, routing synthetic test matches to the test generator."""
    if match_id == constants.TEST_MATCH_ID:
        return await synthetic_match.next_observation(redis_client)
    try:
        return await matches.match_by_id(match_id, redis_client=redis_client)
    except ScrapingError as exc:
        if exc.upstream_status == 404:
            raise NotFoundError("Match not found") from exc
        raise
