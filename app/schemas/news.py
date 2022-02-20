from pydantic import BaseModel


class NewsItem(BaseModel):
    id: str
    title: str
