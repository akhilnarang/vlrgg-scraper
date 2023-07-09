import json

from fastapi import APIRouter

from app import cache, schemas
from app.services import events

router = APIRouter()


@router.get("/")
async def list_events() -> list[schemas.Event]:
    try:
        return [schemas.Event.model_validate(event) for event in json.loads(await cache.get("events"))]
    except cache.CacheMiss:
        return await events.get_events()


@router.get("/{id}")
async def event_by_id(id: str) -> schemas.EventWithDetails:
    return await events.get_event_by_id(id)
