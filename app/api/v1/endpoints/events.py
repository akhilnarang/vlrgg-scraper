import json
from typing import Any

from fastapi import APIRouter

from app import cache, schemas
from app.services import events

router = APIRouter()


@router.get("/", response_model=list[schemas.Event])
async def list_events() -> Any:
    try:
        return [schemas.Event.parse_obj(event) for event in json.loads(await cache.get("events"))]
    except cache.CacheMiss:
        return await events.get_events()


@router.get("/{id}", response_model=schemas.EventWithDetails)
async def event_by_id(id: str) -> Any:
    return await events.get_event_by_id(id)
