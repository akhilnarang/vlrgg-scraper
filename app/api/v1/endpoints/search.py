from typing import Annotated

from fastapi import APIRouter, Query

from app import schemas, constants
from app.services import search

router = APIRouter()


@router.get("/")
async def get_search_results(
    search_category: Annotated[constants.SearchCategory, Query(description="The category you want to search under")],
    search_term: Annotated[str, Query(description="The term you want to search for")],
) -> list[schemas.SearchResult]:
    return await search.get_data(search_category, search_term)
