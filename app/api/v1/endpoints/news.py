import json

from fastapi import APIRouter

from app import cache, schemas
from app.services import news

router = APIRouter()


@router.get("/")
async def get_news() -> list[schemas.NewsItem]:
    if data := await cache.get("news"):
        return [schemas.NewsItem.model_validate(news_item) for news_item in json.loads(data)]
    return await news.news_list()


@router.get("/{news_id}")
async def get_news_by_id(news_id: str) -> schemas.NewsArticle:
    return await news.news_by_id(news_id)
