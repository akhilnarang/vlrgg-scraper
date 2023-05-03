from datetime import datetime

from pydantic import BaseModel, HttpUrl


class Player(BaseModel):
    id: str
    name: str | None
    alias: str
    role: str | None
    img: HttpUrl


class MatchBase(BaseModel):
    id: str
    event: str
    stage: str
    opponent: str
    date: datetime


class UpcomingMatch(MatchBase):
    eta: str | None


class CompletedMatch(MatchBase):
    score: str


# Response for `GET /api/v1/team/{id}`
class Team(BaseModel):
    name: str
    tag: str
    img: HttpUrl
    website: HttpUrl | None
    twitter: str | None
    country: str
    rank: int
    region: str
    roster: list[Player]
    upcoming: list[UpcomingMatch]
    completed: list[CompletedMatch]
