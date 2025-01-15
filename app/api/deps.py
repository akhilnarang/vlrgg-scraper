from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.cache import get_client
from app.core.config import settings

http_bearer = HTTPBearer()


def verify_token(
    token_data: HTTPAuthorizationCredentials = Depends(http_bearer),
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
    raise HTTPException(detail="Invalid token", status_code=status.HTTP_401_UNAUTHORIZED)


async def get_redis_client() -> AsyncGenerator:
    """
    Function to get a redis client
    :return: The redis client object
    """
    client = get_client()
    try:
        yield client
    finally:
        await client.close()


def verify_internal_token(
    token_data: HTTPAuthorizationCredentials = Depends(http_bearer),
) -> None:
    """
    Verify the token for internal APIs

    :param token_data: The token data
    :return: Nothing
    """

    if token_data.credentials != settings.INTERNAL_API_KEY:
        raise HTTPException(detail="Invalid token", status_code=status.HTTP_401_UNAUTHORIZED)
