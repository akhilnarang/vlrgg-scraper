from pydantic import BaseModel, HttpUrl


class Event(BaseModel):
    id: int
    title: str
    status: str
    prize: str
    dates: str
    location: str
    img: HttpUrl


class Prize(BaseModel):
    position: str
    prize: str
    team: str


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
    seed: str
    roster: list[Player]


class MatchTeam(BaseModel):
    name: str
    region: str
    score: int | None


class Match(BaseModel):
    id: str
    time: str
    teams: list[MatchTeam]
    status: str
    eta: str | None
    round: str
    stage: str


class MatchDay(BaseModel):
    date: str
    matches: list[Match]


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
    matches: list[MatchDay]
