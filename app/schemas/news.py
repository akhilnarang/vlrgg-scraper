from pydantic import BaseModel, HttpUrl


class NewsItem(BaseModel):
    url: HttpUrl
    title: str
    description: str
    date: str
    author: str
