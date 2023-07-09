import json

from fastapi import APIRouter

from app import cache, schemas
from app.services import matches

router = APIRouter()


@router.get("/")
async def get_matches() -> list[schemas.Match]:
    try:
        return [schemas.Match.model_validate(match) for match in json.loads(await cache.get("matches"))]
    except cache.CacheMiss:
        return await matches.match_list()


@router.get("/{id}")
async def get_match_by_id(id: str) -> schemas.MatchWithDetails:
    return await matches.match_by_id(id)
