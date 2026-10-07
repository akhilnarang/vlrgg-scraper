from typing import Annotated

from fastapi import APIRouter, Query

from app import schemas
from app.api import deps
from app.services import team_rankings

router = APIRouter()


@router.get("/")
async def get_rankings(
    session: deps.DatabaseSessionDep,
    query: Annotated[schemas.RankingQuery, Query()],
) -> schemas.RankingListResponse:
    """Rank teams by stored Elo, optionally within one circuit."""
    return await team_rankings.rank_teams(session, query)


@router.get("/predict")
async def predict_match(
    session: deps.DatabaseSessionDep,
    query: Annotated[schemas.PredictQuery, Query()],
) -> schemas.PredictResponse:
    """Predict a series with source and warnings, plus generic map Elo."""
    return await team_rankings.predict(session, query.team_a, query.team_b)


@router.get("/teams/{team_id}")
async def get_team_ranking(
    session: deps.DatabaseSessionDep,
    team_id: deps.TeamId,
) -> schemas.TeamRankingProfileResponse:
    """Read a team's Elo, ranks, form, and recent results."""
    return await team_rankings.team_profile(session, team_id)
