from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import get_client
from app.core import connections
from app.core.config import settings
from app.exceptions import ServiceUnavailableError, UnauthorizedError
from app.services.subscription_store import SubscriptionStore

http_bearer = HTTPBearer()


def verify_token(
    token_data: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)],
) -> None:
    """
    Verify the token and return the source/owner
    :param token_data: The token data
    :return: Nothing
    """

    for api_key_source, api_key in settings.API_KEYS.items():
        if token_data.credentials == api_key:
            if settings.SENTRY_DSN:
                import sentry_sdk

                sentry_sdk.set_tag("api_key", api_key_source)
            return
    raise UnauthorizedError(detail="Invalid token")


async def get_redis_client() -> AsyncGenerator:
    """
    Function to get a redis client
    :return: The redis client object
    """
    client = get_client()
    try:
        yield client
    finally:
        await client.aclose()


def require_live_push() -> None:
    """Reject live-update requests while the feature is disabled."""
    if not settings.ENABLE_LIVE_PUSH:
        raise ServiceUnavailableError("Live updates are disabled")


async def get_subscription_session() -> AsyncGenerator[AsyncSession]:
    """Yield one request transaction, committed on success and rolled back on error.

    :return: Request-scoped session.
    :raises ServiceUnavailableError: If the store is absent or fails.
    """
    sessions = connections.subscription_sessions
    if sessions is None:
        raise ServiceUnavailableError("Live updates are unavailable")
    try:
        async with sessions.begin() as session:
            yield session
    except SQLAlchemyError as exc:
        raise ServiceUnavailableError("Subscription store is unavailable") from exc


def get_subscription_store(
    # Function scope commits before the response is sent, so a failed commit returns 503, not 2xx.
    session: Annotated[AsyncSession, Depends(get_subscription_session, scope="function")],
) -> SubscriptionStore:
    """Bind a subscription store to the request session.

    :param session: Request-scoped database session.
    :return: Subscription store.
    """
    return SubscriptionStore(session)


def set_no_store(response: Response) -> None:
    """Prevent caching of private live-update responses."""
    response.headers["Cache-Control"] = "no-store"


def verify_internal_token(
    token_data: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)],
) -> None:
    """
    Verify the token for internal APIs

    :param token_data: The token data
    :return: Nothing
    """

    if token_data.credentials != settings.INTERNAL_API_KEY:
        raise UnauthorizedError(detail="Invalid token")
