import itertools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from redis.asyncio import ConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.constants import REQUEST_TIMEOUT
from app.core.config import settings
from app.services.apns import APNsClient

redis_pool: ConnectionPool | None = None
http_client: httpx2.AsyncClient | None = None
apns_client: APNsClient | None = None
database_engine: AsyncEngine | None = None
subscription_sessions: async_sessionmaker[AsyncSession] | None = None
_user_agents = itertools.cycle(settings.USER_AGENTS)


async def rotate_user_agent(request: httpx2.Request) -> None:
    """Set the next configured user agent on an outgoing request.

    :param request: Outgoing HTTP request.
    :return: None.
    """
    request.headers["User-Agent"] = next(_user_agents)


class RotatingAddressTransport(httpx2.AsyncBaseTransport):
    """Rotate one request at a time across the configured local addresses.

    Direct callers may pass an empty list to leave requests unbound and let the
    kernel pick the source. build_transport() returns None for empty settings so
    httpx retains its default transport and environment-proxy discovery.
    """

    def __init__(self, addresses: list[str]) -> None:
        self._transports = [httpx2.AsyncHTTPTransport(local_address=address) for address in addresses] or [
            httpx2.AsyncHTTPTransport()
        ]
        self._cycle = itertools.cycle(self._transports)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        """Send a request using the next address in round-robin order.

        :param request: Outgoing HTTP request.
        :return: HTTP response from the selected transport.
        """
        return await next(self._cycle).handle_async_request(request)

    async def aclose(self) -> None:
        """Close each underlying transport."""
        for transport in self._transports:
            await transport.aclose()


def build_transport() -> httpx2.AsyncBaseTransport | None:
    """Build the HTTP transport for the configured local addresses.

    :return: A rotating transport, or None for httpx's default and proxy discovery.
    """
    addresses = settings.HTTP_LOCAL_ADDRESSES
    return RotatingAddressTransport(addresses) if addresses else None


@asynccontextmanager
async def get_http_client() -> AsyncIterator[httpx2.AsyncClient]:
    """Yield the shared HTTP client, or a temporary one if not initialized (e.g. tests)."""
    if http_client is not None:
        yield http_client
    else:
        async with httpx2.AsyncClient(
            transport=build_transport(),
            timeout=REQUEST_TIMEOUT,
            headers={},
            event_hooks={"request": [rotate_user_agent]},
        ) as client:
            yield client
