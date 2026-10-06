from fastapi import APIRouter

from app.api.v2.endpoints.rankings import router as rankings_router

router = APIRouter()

router.include_router(rankings_router, prefix="/rankings", tags=["Rankings"])
