import time

from fastapi import APIRouter

from app import cache, schemas
from app.api import deps
from app.core.config import settings
from app.services import insights, match_vods, matches

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
    details = await matches.cached_match_by_id(id, client)
    response = await insights.attach(id, details, deadline)
    response.map_vods = await match_vods.map_vods(id, details)
    return response
