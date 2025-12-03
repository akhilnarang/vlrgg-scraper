import json

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from app import schemas, cache
from app.api import deps
from app.services import events

router = APIRouter()


@router.get("/")
async def list_events(client: Redis = Depends(deps.get_redis_client)) -> list[schemas.Event]:
    if data := await cache.get("events", client=client):
        return [schemas.Event.model_validate(event) for event in json.loads(data)]
    return await events.get_events(client)


@router.get("/{id}")
async def event_by_id(id: str, client: Redis = Depends(deps.get_redis_client)) -> schemas.EventWithDetails:
    return await events.get_event_by_id(id, client)
