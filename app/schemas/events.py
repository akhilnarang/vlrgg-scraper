from pydantic import BaseModel, HttpUrl


class Event(BaseModel):
    id: int
    title: str
    status: str
    prize: str
    dates: str
    location: str
    img: HttpUrl
