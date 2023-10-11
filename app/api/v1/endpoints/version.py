from fastapi import APIRouter

from app import schemas

router = APIRouter()


@router.get("/")
async def get_versions() -> schemas.VersionResponse:
    return schemas.VersionResponse(
        event_list=1,
        event_details=1,
        match_list=1,
        match_details=1,
        news_list=1,
        player_details=1,
        rankings_list=1,
        team_details=1,
    )
