from fastapi import APIRouter

from app.api.v1.endpoints.events import router as events_router
from app.api.v1.endpoints.matches import router as matches_router
from app.api.v1.endpoints.news import router as news_router

router = APIRouter()

router.include_router(events_router, prefix="/events", tags=["Events"])
router.include_router(matches_router, prefix="/matches", tags=["Matches"])
router.include_router(news_router, prefix="/news", tags=["News"])
