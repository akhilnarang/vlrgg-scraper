from fastapi import APIRouter

from app import schemas
from app.services import team

router = APIRouter()


@router.get("/{team_id}", response_model=schemas.Team)
async def get_team_by_id(team_id: str) -> schemas.Team:
    return await team.get_team_data(team_id)
