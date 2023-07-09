import json

from fastapi import APIRouter

from app import cache, schemas
from app.services import news

router = APIRouter()


@router.get("/")
async def get_news() -> list[schemas.NewsItem]:
    try:
        return [schemas.NewsItem.model_validate(news_item) for news_item in json.loads(await cache.get("news"))]
    except cache.CacheMiss:
        return await news.news_list()
