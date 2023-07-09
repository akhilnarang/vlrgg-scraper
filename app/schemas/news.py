from datetime import datetime

from pydantic import BaseModel, HttpUrl


# Response for `GET /api/v1/news`
class NewsItem(BaseModel):
    url: HttpUrl
    title: str
    description: str
    date: datetime
    author: str
