from fastapi import APIRouter

from app import schemas
from app.services import player

router = APIRouter()


@router.get("/{player_id}")
async def get_player_by_id(player_id: str) -> schemas.Player:
    return await player.get_player_data(player_id)
