from typing import Any

from fastapi import APIRouter

from app import schemas
from app.services import matches

router = APIRouter()


@router.get("/", response_model=list[schemas.Match])
async def get_matches() -> Any:
    return await matches.match_list()


@router.get("/{id}", response_model=schemas.MatchWithDetails)
async def get_match_by_id(id: str) -> Any:
    return await matches.match_by_id(id)
