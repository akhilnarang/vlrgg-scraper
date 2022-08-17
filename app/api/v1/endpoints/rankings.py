import json
from typing import Any

from fastapi import APIRouter

from app import cache, schemas
from app.services import rankings

router = APIRouter()


@router.get("/", response_model=list[schemas.Ranking])
async def get_rankings() -> Any:
    try:
        return [schemas.Ranking.parse_obj(ranking) for ranking in json.loads(await cache.get("rankings"))]
    except cache.CacheMiss:
        return await rankings.ranking_list()
