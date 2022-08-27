import json

from fastapi import APIRouter

from app import cache, schemas
from app.services import rankings

router = APIRouter()


@router.get("/")
async def get_rankings() -> list[schemas.Ranking]:
    try:
        return [schemas.Ranking.model_validate(ranking) for ranking in json.loads(await cache.get("rankings"))]
    except cache.CacheMiss:
        return await rankings.ranking_list()
