import time

from fastapi import APIRouter

from app import cache, constants, schemas
from app.api import deps
from app.core.config import settings
from app.services import insights, matches

router = APIRouter()


@router.get("/")
async def get_matches(client: deps.RedisDep) -> list[schemas.ListedMatch]:
    if data := await cache.get("matches", client=client):
        listed = schemas.MatchListAdapter.validate_json(data)
    else:
        listed = await matches.match_list(redis_client=client)
    return await matches.with_broadcast_map(listed, client)


@router.get("/{id}")
async def get_match_by_id(id: deps.MatchId, client: deps.RedisDep) -> schemas.MatchDetailsResponse:
    deadline = time.monotonic() + settings.INSIGHTS_REQUEST_BUDGET_SECONDS
    cache_key = f"match:{id}"
    if data := await cache.get(cache_key, client=client):
        details = schemas.MatchWithDetails.model_validate_json(data)
    else:
        details = await matches.match_by_id(id, client)
        await cache.set(cache_key, details.model_dump_json(), ttl=constants.CACHE_TTL_MATCH, client=client)
    return await insights.attach(id, details, deadline)
