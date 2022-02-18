from typing import Any

from fastapi import APIRouter

from app import schemas
from app.services import events

router = APIRouter()


@router.get("/", response_model=list[schemas.Event])
async def list_events() -> Any:
    return await events.get_events()
