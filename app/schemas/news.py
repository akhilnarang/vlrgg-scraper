from datetime import datetime

from pydantic import BaseModel


# Response for `GET /api/v1/news`
class NewsItem(BaseModel):
    url: str
    title: str
    description: str
    date: datetime
    author: str


# Response for `GET /api/v1/news/{id}`
class NewsArticle(BaseModel):
    id: str
    title: str
    content: str
    links: list[dict[str, str]]
    images: list[str]
    videos: list[str]
    date: datetime | None
    author: str
