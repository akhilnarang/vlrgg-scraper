from fastapi import APIRouter

from app.api.v1.endpoints.events import router as events_router

router = APIRouter()

router.include_router(events_router, prefix="/events", tags=["Events"])
