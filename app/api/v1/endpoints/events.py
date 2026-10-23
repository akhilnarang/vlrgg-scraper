from fastapi import APIRouter

from app import cache, schemas
from app.api import deps
from app.services import brackets, events

router = APIRouter()


@router.get("/")
async def list_events(client: deps.RedisDep) -> list[schemas.Event]:
    if data := await cache.get("events", client=client):
        return schemas.EventListAdapter.validate_json(data)
    return await events.get_events(client)


@router.get("/{id}/brackets")
async def event_brackets(id: deps.EventId, client: deps.RedisDep) -> schemas.EventBrackets:
    """Return Riot's bracket layout for an event, with a status saying what it does not cover."""
    return await brackets.event_brackets(id, client)


@router.get("/{id}")
async def event_by_id(id: str, client: deps.RedisDep) -> schemas.EventWithDetails:
    return await events.get_event_by_id(id, client)
