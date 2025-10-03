import asyncio
from contextvars import ContextVar
from redis.asyncio import ConnectionPool, Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
import httpx

from app.core.config import settings

redis_pool: ConnectionPool | None = None

# SQLAlchemy
engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Context variable for per-request Redis client
redis_client_var: ContextVar[Redis | None] = ContextVar("redis_client")  # type: ignore


class RedisSemaphore:
    """Distributed semaphore using Redis for cross-process limiting."""

    def __init__(self, key: str, limit: int, client_var: ContextVar[Redis | None]):
        self.key = key
        self.limit = limit
        self.client_var = client_var

    async def __aenter__(self):
        if redis_pool is None:
            # No Redis, no limiting
            return
        while True:
            client = self.client_var.get()
            if client is None:
                # Fallback for background tasks without request context
                client = Redis(connection_pool=redis_pool)
                try:
                    current = await client.incr(self.key)
                    if current <= self.limit:
                        # Keep client open for __aexit__
                        self._fallback_client = client
                        return
                    # Over limit, decrement and close
                    await client.decr(self.key)
                    await client.aclose()
                except:
                    await client.aclose()
                    raise
            else:
                current = await client.incr(self.key)
                if current <= self.limit:
                    return
                # Over limit, decrement
                await client.decr(self.key)
            await asyncio.sleep(0.1)  # Wait before retry

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if redis_pool is None:
            # No Redis, no limiting
            return
        client = self.client_var.get()
        if client is not None:
            await client.decr(self.key)
        elif hasattr(self, "_fallback_client"):
            try:
                await self._fallback_client.decr(self.key)
            finally:
                await self._fallback_client.aclose()
                del self._fallback_client


# Semaphore to limit concurrent requests to VLR.gg
vlr_request_semaphore = RedisSemaphore(
    key="vlr_request_semaphore", limit=settings.MAX_CONCURRENT_VLR_REQUESTS, client_var=redis_client_var
)


def get_vlr_client() -> httpx.AsyncClient:
    """Get httpx client configured for VLR.gg requests with gzip compression and connection pooling."""
    return httpx.AsyncClient(
        timeout=30.0,
        headers={"Accept-Encoding": "gzip", "User-Agent": "vlrgg-scraper/1.0"},
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )
