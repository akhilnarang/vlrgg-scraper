from datetime import datetime

from pydantic import BaseModel, HttpUrl, field_serializer
from pydantic_core.core_schema import SerializationInfo

from app.schemas.base import fix_datetime_tz


# Response for `GET /api/v1/news`
class NewsItem(BaseModel):
    url: HttpUrl
    title: str
    description: str
    date: datetime
    author: str

    @field_serializer("date")
    def serialize_dt(self, value: datetime, _info: SerializationInfo) -> str:
        return fix_datetime_tz(value)
