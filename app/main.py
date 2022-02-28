import os
import time

from fastapi import Depends, FastAPI
from starlette.middleware.gzip import GZipMiddleware

from app.api import deps
from app.api.v1.api import router

app = FastAPI(title="Scraper", description="Scraper for VLR.gg that exposes a REST API for some data available there")
app.add_middleware(GZipMiddleware)
app.include_router(router, prefix="/api/v1", dependencies=[Depends(deps.verify_token)])

# Ensure that our timezone is set to UTC to make sure that time values are returned correctly
os.environ["TZ"] = "UTC"
time.tzset()
