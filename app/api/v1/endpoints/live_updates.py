"""Minimal client token and favorites endpoints for live updates."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis

from app import constants
from app.api import deps
from app.exceptions import ConflictError
from app.schemas.matches import Favorites, TokenRegistration
from app.services.subscription_store import SubscriptionStore

router = APIRouter(
    dependencies=[Depends(deps.verify_token), Depends(deps.require_live_push), Depends(deps.set_no_store)]
)


@router.post("/test-match", status_code=status.HTTP_204_NO_CONTENT)
async def trigger_test_match(client: Annotated[Redis, Depends(deps.get_redis_client)]) -> None:
    """Start the synthetic test match; returns 409 while one is already running."""
    if not await client.set(constants.TEST_TICK_KEY, 0, nx=True):
        raise ConflictError("Test match is already running")


@router.put("/clients/{client_id}/token", status_code=status.HTTP_204_NO_CONTENT)
async def put_token(
    client_id: UUID,
    body: TokenRegistration,
    store: Annotated[SubscriptionStore, Depends(deps.get_subscription_store)],
) -> None:
    """Store a client's APNs push-to-start token."""
    await store.register_token(str(client_id), body.token)


@router.delete("/clients/{client_id}/token", status_code=status.HTTP_204_NO_CONTENT)
async def delete_token(
    client_id: UUID,
    store: Annotated[SubscriptionStore, Depends(deps.get_subscription_store)],
) -> None:
    """Delete a client's APNs push-to-start token."""
    await store.delete_token(str(client_id))


@router.put("/clients/{client_id}/favorites", status_code=status.HTTP_204_NO_CONTENT)
async def put_favorites(
    client_id: UUID,
    body: Favorites,
    store: Annotated[SubscriptionStore, Depends(deps.get_subscription_store)],
) -> None:
    """Replace a client's favorites."""
    await store.replace_favorites(str(client_id), body)


@router.get("/clients/{client_id}/favorites")
async def get_favorites(
    client_id: UUID,
    store: Annotated[SubscriptionStore, Depends(deps.get_subscription_store)],
) -> Favorites:
    """Read a client's favorites."""
    return await store.get_favorites(str(client_id))
