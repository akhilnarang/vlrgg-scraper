from datetime import datetime

from pydantic import BaseModel, HttpUrl

from app.constants import MatchStatus


class Team(BaseModel):
    name: str
    score: int | None = None


class TeamWithImage(Team):
    img: HttpUrl
    id: str | None = None


class Event(BaseModel):
    id: str
    img: HttpUrl
    series: str
    stage: str
    date: datetime | None = None
    patch: str | None = None
    status: str | None = None


class Agent(BaseModel):
    title: str
    img: HttpUrl


class TeamMember(BaseModel):
    id: str
    name: str
    team: str
    agents: list[Agent]
    rating: float
    acs: int
    kills: int
    deaths: int
    assists: int
    kast: int
    adr: int
    headshot_percent: int
    first_kills: int
    first_deaths: int
    first_kills_diff: int


class Round(BaseModel):
    round_number: int
    round_score: str
    winner: str
    side: str
    win_type: str


class MatchData(BaseModel):
    map: str = ""
    teams: list[Team]
    members: list[TeamMember]
    rounds: list[Round]


class PreviousEncounters(BaseModel):
    teams: list[Team]
    match_id: str


class Video(BaseModel):
    name: str
    url: HttpUrl


class MatchVideos(BaseModel):
    streams: list[Video]
    vods: list[Video]


# Response for `GET /api/v1/matches/{match_id}`
class MatchWithDetails(BaseModel):
    teams: list[TeamWithImage]
    bans: list[str]
    event: Event
    videos: MatchVideos
    map_count: int
    data: list[MatchData]
    previous_encounters: list[PreviousEncounters]


class MatchTeam(BaseModel):
    name: str
    id: str | None = None
    score: int | None = None


# Response for `GET /api/v1/matches`
class Match(BaseModel):
    id: str
    team1: MatchTeam
    team2: MatchTeam
    status: MatchStatus
    time: datetime
    event: str
    series: str
    event_id: str | None = None
