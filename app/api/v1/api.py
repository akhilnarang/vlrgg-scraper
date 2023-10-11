from fastapi import APIRouter

from app.api.v1.endpoints.events import router as events_router
from app.api.v1.endpoints.matches import router as matches_router
from app.api.v1.endpoints.news import router as news_router
from app.api.v1.endpoints.player import router as player_router
from app.api.v1.endpoints.rankings import router as rankings_router
from app.api.v1.endpoints.team import router as team_router
from app.api.v1.endpoints.version import router as version_router

router = APIRouter()

router.include_router(events_router, prefix="/events", tags=["Events"])
router.include_router(matches_router, prefix="/matches", tags=["Matches"])
router.include_router(news_router, prefix="/news", tags=["News"])
router.include_router(team_router, prefix="/team", tags=["Team"])
router.include_router(player_router, prefix="/player", tags=["Player"])
router.include_router(rankings_router, prefix="/rankings", tags=["Rankings"])
router.include_router(version_router, prefix="/version", tags=["Version"])
