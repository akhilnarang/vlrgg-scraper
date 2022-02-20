from typing import Any

from fastapi import APIRouter

from app import schemas
from app.services import news

router = APIRouter()


@router.get("/", response_model=list[schemas.NewsItem])
async def get_news() -> Any:
    return await news.news_list()
