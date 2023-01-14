from datetime import datetime

from pydantic import BaseModel, HttpUrl

from app.schemas.base import fix_datetime_tz


class NewsItem(BaseModel):
    url: HttpUrl
    title: str
    description: str
    date: datetime
    author: str

    class Config:
        json_encoders = {datetime: fix_datetime_tz}
