from typing import Annotated

from fastapi import APIRouter, Query

from app import schemas
from app.services import player

router = APIRouter()


@router.get("/{player_id}")
async def get_player_by_id(
    player_id: str,
    match_pages: Annotated[
        int,
        Query(description="Pages of match history to include (50 per page); <= 0 fetches all."),
    ] = 1,
) -> schemas.Player:
    return await player.get_player_data(player_id, match_pages=match_pages)
