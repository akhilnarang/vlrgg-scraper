from typing import Any

from fastapi import APIRouter

from app import schemas
from app.services import matches

router = APIRouter()


@router.get("/{id}", response_model=schemas.Match)
async def get_match_by_id(id: str) -> Any:
    return await matches.match_by_id(id)
