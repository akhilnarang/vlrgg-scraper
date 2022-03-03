import os
import time

from fastapi import Depends, FastAPI
from starlette.middleware.gzip import GZipMiddleware

from app.api import deps
from app.api.v1.api import router
from app.core.config import settings

app = FastAPI(title="Scraper", description="Scraper for VLR.gg that exposes a REST API for some data available there")
app.add_middleware(GZipMiddleware)

if settings.SECRET_KEY:
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(deps.verify_token)])
else:
    app.include_router(router, prefix="/api/v1")

# Ensure that our timezone is set to UTC to make sure that time values are returned correctly
os.environ["TZ"] = "UTC"
time.tzset()
