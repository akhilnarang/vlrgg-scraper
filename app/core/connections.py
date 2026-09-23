from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from redis.asyncio import ConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.services.apns import APNsClient

redis_pool: ConnectionPool | None = None
http_client: httpx.AsyncClient | None = None
apns_client: APNsClient | None = None
database_engine: AsyncEngine | None = None
subscription_sessions: async_sessionmaker[AsyncSession] | None = None


@asynccontextmanager
async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield the shared HTTP client, or a temporary one if not initialized (e.g. tests)."""
    if http_client is not None:
        yield http_client
    else:
        from app.constants import REQUEST_TIMEOUT, USER_AGENT

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            yield client
