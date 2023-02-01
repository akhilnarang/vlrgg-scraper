from datetime import datetime

from pydantic import BaseModel, HttpUrl

from app.constants import EventStatus, MatchStatus
from app.schemas.base import fix_datetime_tz


class Event(BaseModel):
    id: int
    title: str
    status: str
    prize: str
    dates: str
    location: str
    img: HttpUrl

    class Config:
        json_encoders = {datetime: fix_datetime_tz}


class PrizeTeam(BaseModel):
    id: str
    name: str
    img: HttpUrl
    country: str


class Prize(BaseModel):
    position: str
    prize: str
    team: PrizeTeam | None


class Player(BaseModel):
    id: str
    name: str
    country: str


class Team(BaseModel):
    name: str
    id: str
    img: HttpUrl
    seed: str | None
    # roster: list[Player]


class MatchTeam(BaseModel):
    name: str
    region: str
    score: int | None


class Match(BaseModel):
    id: str
    time: str
    date: datetime
    eta: str | None
    status: MatchStatus
    teams: list[MatchTeam]
    round: str
    stage: str


class EventWithDetails(BaseModel):
    id: str
    title: str
    subtitle: str
    dates: str
    prize: str
    location: str
    status: EventStatus
    img: HttpUrl
    prizes: list[Prize] = []
    teams: list[Team] = []
    matches: list[Match]

    class Config:
        json_encoders = {datetime: fix_datetime_tz}
