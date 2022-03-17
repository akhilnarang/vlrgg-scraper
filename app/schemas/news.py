from datetime import datetime

from pydantic import BaseModel, HttpUrl


class NewsItem(BaseModel):
    url: HttpUrl
    title: str
    description: str
    date: datetime
    author: str
