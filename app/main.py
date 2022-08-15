import logging
import sys

import sentry_sdk
from brotli_asgi import BrotliMiddleware
from fastapi import Depends, FastAPI
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.api import deps
from app.api.v1.api import router
from app.core.config import settings

logging.basicConfig(
    format="[%(levelname)s] (%(asctime)s) %(module)s:%(pathname)s:%(funcName)s:%(lineno)s:: %(message)s",
    level=logging.INFO,
    datefmt="%d-%m-%y %H:%M:%S",
    stream=sys.stdout,
)

# Initialize Sentry SDK if a DSN is defined in our environment
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
        ],
        traces_sample_rate=1.0,
    )


app = FastAPI(title="Scraper", description="Scraper for VLR.gg that exposes a REST API for some data available there")
app.add_middleware(BrotliMiddleware)

if settings.SECRET_KEY:
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(deps.verify_token)])
else:
    app.include_router(router, prefix="/api/v1")
