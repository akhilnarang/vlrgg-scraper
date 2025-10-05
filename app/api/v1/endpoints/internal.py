from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from redis.asyncio import Redis

from app.api.deps import get_redis_client, verify_internal_token
from app.schemas import TeamCache

router = APIRouter()


@router.get("/team_cache")
async def get_team_cache(client: Redis = Depends(get_redis_client)) -> list[TeamCache]:
    return [TeamCache(name=k.decode(), id=v.decode()) for k, v in (await client.hgetall("team")).items()]  # type: ignore


@router.put("/team_cache")
async def set_team_cache(teams: list[TeamCache], client: Redis = Depends(get_redis_client)) -> list[TeamCache]:
    await client.hset("team", mapping={team.name: team.id for team in teams})  # type: ignore
    return [TeamCache(name=k.decode(), id=v.decode()) for k, v in (await client.hgetall("team")).items()]  # type: ignore


@router.get("/database", dependencies=[Depends(verify_internal_token)])
async def get_database() -> FileResponse:
    return FileResponse(path="sqlite.db", media_type="application/vnd.sqlite3", filename="sqlite.db")
