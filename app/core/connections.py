from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from redis.asyncio import ConnectionPool

redis_pool: ConnectionPool | None = None
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield the shared HTTP client, or a temporary one if not initialized (e.g. tests)."""
    if http_client is not None:
        yield http_client
    else:
        from app.constants import REQUEST_TIMEOUT

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            yield client
