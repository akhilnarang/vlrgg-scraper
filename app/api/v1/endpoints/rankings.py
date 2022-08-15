from typing import Any

from fastapi import APIRouter

from app import schemas
from app.services import rankings

router = APIRouter()


@router.get("/", response_model=list[schemas.Ranking])
async def get_rankings() -> Any:
    return await rankings.ranking_list()
