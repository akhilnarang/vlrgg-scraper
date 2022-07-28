from brotli_asgi import BrotliMiddleware
from fastapi import Depends, FastAPI

from app.api import deps
from app.api.v1.api import router
from app.core.config import settings

app = FastAPI(title="Scraper", description="Scraper for VLR.gg that exposes a REST API for some data available there")
app.add_middleware(BrotliMiddleware)

if settings.SECRET_KEY:
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(deps.verify_token)])
else:
    app.include_router(router, prefix="/api/v1")
