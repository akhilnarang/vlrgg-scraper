from pydantic import BaseModel, HttpUrl

from app.constants import MatchStatus


class Event(BaseModel):
    id: int
    title: str
    status: str
    prize: str
    dates: str
    location: str
    img: HttpUrl


class PrizeTeam(BaseModel):
    id: str
    name: str
    img: HttpUrl
    country: str


class Prize(BaseModel):
    position: str
    prize: str
    team: PrizeTeam | None


class Bracket(BaseModel):
    upper: list = []
    lower: list = []


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
    date: str
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
    img: HttpUrl
    prizes: list[Prize] = []
    brackets: list[Bracket]
    teams: list[Team]
    matches: list[Match]
