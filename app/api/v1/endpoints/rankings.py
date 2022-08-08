from typing import Any

from fastapi import APIRouter, Query

from app import schemas
from app.services import rankings

router = APIRouter()


@router.get("/", response_model=list[schemas.Ranking])
async def get_rankings(
    limit: int = Query(20, description="The number of teams per region you want rankings for")
) -> Any:
    return await rankings.ranking_list(limit)
