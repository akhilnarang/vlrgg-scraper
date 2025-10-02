import json
from datetime import datetime

from fastapi import APIRouter, Path

from app import cache, schemas
from app.services import standings

router = APIRouter()

current_year = datetime.now().year


@router.get("/{year}")
async def get_standings(year: int = Path(..., ge=2021, le=current_year)) -> schemas.Standings:
    cache_key = f"standings_{year}"
    if data := await cache.get(cache_key):
        return schemas.Standings.model_validate(json.loads(data))

    return await standings.standings_list(year)
