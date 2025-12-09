import logging
import socket
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

import redis.asyncio as redis
import sentry_sdk
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Response, Request
from fastapi.middleware.gzip import GZipMiddleware
from rich.logging import RichHandler
from sentry_sdk.integrations.arq import ArqIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.api import deps
from app.api.v1.api import router
from app.api.v1.endpoints.internal import router as internal_router
from app.core import connections
from app.core.config import settings
from app.cron import arq_worker
from app.utils import before_send

logging.basicConfig(
    format="[%(levelname)s] (%(asctime)s) %(module)s:%(pathname)s:%(funcName)s:%(lineno)s:: %(message)s",
    level=logging.INFO,
    datefmt="%d-%m-%y %H:%M:%S",
    handlers=[RichHandler(rich_tracebacks=True)],
)

# Initialize Sentry SDK if a DSN is defined in our environment
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            HttpxIntegration(),
            ArqIntegration(),
        ],
        traces_sample_rate=0.08,
        before_send=before_send,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator:
    logging.info("Connecting to redis")
    connections.redis_pool = redis.ConnectionPool(
        host=settings.REDIS_HOST,
        password=settings.REDIS_PASSWORD,
        port=settings.REDIS_PORT,
    )
    logging.info("Starting arq worker")
    await arq_worker.start(
        handle_signals=False,
        redis_settings=RedisSettings(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
        ),
    )
    yield
    logging.info("Stopping arq worker")
    await arq_worker.stop()
    logging.info("Closing redis connection pool")
    await connections.redis_pool.aclose()


app_lifespan = None
if settings.ENABLE_CACHE:
    app_lifespan = lifespan

app = FastAPI(
    title="Scraper",
    description="Scraper for VLR.gg that exposes a REST API for some data available there",
    lifespan=app_lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=500)  # type: ignore[arg-type]


@app.middleware("http")
async def add_server_name_header(request: Request, call_next: Callable) -> Response:
    response = await call_next(request)
    response.headers["X-Server"] = socket.gethostname()
    return response


if settings.API_KEYS:
    print("Got API keys", settings.API_KEYS.keys())
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(deps.verify_token)])
else:
    app.include_router(router, prefix="/api/v1")
    sentry_sdk.set_tag("api_key", "Unauthenticated")

if settings.ENABLE_CACHE and settings.ENABLE_ID_MAP_DB:
    app.include_router(internal_router, prefix="/api/v1/internal", dependencies=[Depends(deps.verify_internal_token)])
