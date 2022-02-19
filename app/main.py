from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from app.api.v1.api import router

app = FastAPI(title="Scraper", description="Scraper for VLR.gg that exposes a REST API for some data available there")
app.add_middleware(GZipMiddleware)
app.include_router(router, prefix="/api/v1")
