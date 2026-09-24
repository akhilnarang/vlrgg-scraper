import logging
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx2
import redis.asyncio as redis
import sentry_sdk
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.gzip import GZipMiddleware

from app import constants, exceptions, i18n
from app.api import deps
from app.api.v1.api import router
from app.api.v1.endpoints.internal import router as internal_router
from app.core import connections
from app.core.config import settings
from app.core.live_push import start_live_push, stop_live_push
from app.core.observability import configure_logging, init_sentry
from app.cron import arq_worker
from app.db.lifecycle import start_database, stop_database
from app.web.media import router as media_router

logger = logging.getLogger(__name__)

configure_logging()
init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator:
    logger.info("Creating shared HTTP client")
    connections.http_client = httpx2.AsyncClient(
        transport=connections.build_transport(),
        timeout=constants.REQUEST_TIMEOUT,
        headers={},
        event_hooks={"request": [connections.rotate_user_agent]},
    )
    try:
        await start_database()
        if settings.ENABLE_LIVE_PUSH:
            await start_live_push()
        if settings.needs_redis:
            logger.info("Connecting to redis")
            connections.redis_pool = redis.ConnectionPool(
                host=settings.REDIS_HOST,
                password=settings.REDIS_PASSWORD,
                port=settings.REDIS_PORT,
            )
            logger.info("Starting arq worker")
            await arq_worker.start(
                handle_signals=False,
                redis_settings=RedisSettings(
                    host=settings.REDIS_HOST,
                    port=settings.REDIS_PORT,
                    password=settings.REDIS_PASSWORD,
                ),
            )
        yield
    finally:
        if settings.needs_redis:
            logger.info("Stopping arq worker")
            try:
                await arq_worker.stop()
            finally:
                logger.info("Closing redis connection pool")
                if connections.redis_pool:
                    await connections.redis_pool.aclose()
        await stop_live_push()
        await stop_database()
        logger.info("Closing shared HTTP client")
        await connections.http_client.aclose()
        connections.http_client = None


app = FastAPI(
    title="Scraper",
    description="Scraper for VLR.gg that exposes a REST API for some data available there",
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=500)  # type: ignore[arg-type]
exceptions.register_exception_handlers(app)

_HOSTNAME = socket.gethostname()


@app.middleware("http")
async def add_server_name_header(request: Request, call_next: Callable) -> Response:
    response = await call_next(request)
    response.headers["X-Server"] = _HOSTNAME
    return response


app.middleware("http")(i18n.localize_response)


app.include_router(media_router)

if settings.API_KEYS:
    print("Got API keys", settings.API_KEYS.keys())
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(deps.verify_token)])
else:
    app.include_router(router, prefix="/api/v1")
    sentry_sdk.set_tag("api_key", "Unauthenticated")

if settings.ENABLE_ID_MAPPING:
    app.include_router(internal_router, prefix="/api/v1/internal", dependencies=[Depends(deps.verify_internal_token)])
