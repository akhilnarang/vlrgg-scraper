import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import sentry_sdk
from brotli_asgi import BrotliMiddleware
from fastapi import Depends, FastAPI
from rich.logging import RichHandler
from sentry_sdk.integrations.arq import ArqIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.api import deps
from app.api.v1.api import router
from app.core.config import settings
from app.cron import arq_worker

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
            ArqIntegration(),
        ],
        traces_sample_rate=0.2,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator:
    logging.info("Starting arq worker")
    await arq_worker.start(handle_signals=False)
    yield
    logging.info("Stopping arq worker")
    await arq_worker.stop()


app = FastAPI(
    title="Scraper",
    description="Scraper for VLR.gg that exposes a REST API for some data available there",
    lifespan=lifespan,
)
app.add_middleware(BrotliMiddleware)

if settings.SECRET_KEY:
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(deps.verify_token)])
else:
    app.include_router(router, prefix="/api/v1")
