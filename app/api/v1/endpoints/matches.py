from fastapi import APIRouter

from app import cache, constants, schemas
from app.api import deps
from app.services import matches

router = APIRouter()


@router.get("/")
async def get_matches(client: deps.RedisDep) -> list[schemas.Match]:
    if data := await cache.get("matches", client=client):
        return schemas.MatchListAdapter.validate_json(data)
    return await matches.match_list(redis_client=client)


@router.get("/{id}")
async def get_match_by_id(id: str, client: deps.RedisDep) -> schemas.MatchWithDetails:
    cache_key = f"match:{id}"
    if data := await cache.get(cache_key, client=client):
        return schemas.MatchWithDetails.model_validate_json(data)
    result = await matches.match_by_id(id, client)
    await cache.set(cache_key, result.model_dump_json(), ttl=constants.CACHE_TTL_MATCH, client=client)
    return result
