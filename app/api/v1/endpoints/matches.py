import json

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from app import cache, schemas
from app.api import deps
from app.services import matches

router = APIRouter()


@router.get("/")
async def get_matches(client: Redis = Depends(deps.get_redis_client)) -> list[schemas.Match]:
    if data := await cache.get("matches", client=client):
        return [schemas.Match.model_validate(match) for match in json.loads(data)]
    return await matches.match_list(redis_client=client)


@router.get("/{id}")
async def get_match_by_id(id: str, client: Redis = Depends(deps.get_redis_client)) -> schemas.MatchWithDetails:
    return await matches.match_by_id(id, client)
