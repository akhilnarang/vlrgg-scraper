import json
from typing import Any

from fastapi import APIRouter

from app import cache, schemas
from app.services import news

router = APIRouter()


@router.get("/", response_model=list[schemas.NewsItem])
async def get_news() -> Any:
    try:
        return [schemas.NewsItem.parse_obj(news) for news in json.loads(await cache.get("news"))]
    except cache.CacheMiss:
        return await news.news_list()
