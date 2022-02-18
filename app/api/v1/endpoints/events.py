from typing import Any

from fastapi import APIRouter

from app import schemas
from app.services import events

router = APIRouter()


@router.get("/", response_model=list[schemas.Event])
async def list_events() -> Any:
    return await events.get_events()


@router.get("/{id}", response_model=schemas.EventWithDetails)
async def event_by_id(id: str) -> Any:
    return await events.get_event_by_id(id)
