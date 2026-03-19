from datetime import datetime

from fastapi import APIRouter, Path

from app import cache, schemas
from app.exceptions import BadRequestError
from app.services import standings

router = APIRouter()


@router.get("/{year}")
async def get_standings(year: int = Path(..., ge=2021)) -> schemas.Standings:
    if year > datetime.now().year:
        raise BadRequestError(detail=f"Year {year} is in the future")
    cache_key = f"standings_{year}"
    if data := await cache.get(cache_key):
        return schemas.Standings.model_validate_json(data)

    return await standings.standings_list(year)
