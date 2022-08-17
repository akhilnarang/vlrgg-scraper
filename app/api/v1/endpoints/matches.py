import json
from typing import Any

from fastapi import APIRouter

from app import cache, schemas
from app.services import matches

router = APIRouter()


@router.get("/", response_model=list[schemas.Match])
async def get_matches() -> Any:
    try:
        return [schemas.Match.parse_obj(match) for match in json.loads(await cache.get("matches"))]
    except cache.CacheMiss:
        return await matches.match_list()


@router.get("/{id}", response_model=schemas.MatchWithDetails)
async def get_match_by_id(id: str) -> Any:
    return await matches.match_by_id(id)
