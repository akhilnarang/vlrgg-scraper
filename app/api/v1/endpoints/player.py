from typing import Any

from fastapi import APIRouter

from app import schemas
from app.services import player

router = APIRouter()


@router.get("/{player_id}", response_model=schemas.Player)
async def get_player_by_id(player_id: str) -> Any:
    return await player.get_player_data(player_id)
