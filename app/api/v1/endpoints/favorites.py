from uuid import UUID

from fastapi import APIRouter, Depends

from app import schemas
from app.api import deps
from app.services import favorites

router = APIRouter(dependencies=[Depends(deps.set_no_store)])


@router.get("/{client_id}/matches")
async def get_favorite_matches(
    client_id: UUID, session: deps.DatabaseSessionDep, redis_client: deps.RedisDep, include_results: bool = False
) -> list[schemas.Match]:
    """Live and upcoming matches for a client's favorites, plus the last 24 hours of results with include_results."""
    return await favorites.favorite_matches(session, str(client_id), redis_client, include_results)
