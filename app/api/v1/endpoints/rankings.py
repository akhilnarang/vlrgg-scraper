from fastapi import APIRouter

from app import cache, schemas
from app.services import rankings

router = APIRouter()


@router.get("/")
async def get_rankings() -> list[schemas.Ranking]:
    if data := await cache.get("rankings"):
        return schemas.RankingListAdapter.validate_json(data)
    return await rankings.ranking_list()
