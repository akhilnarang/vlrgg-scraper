from pydantic import BaseModel, HttpUrl


class Team(BaseModel):
    name: str
    img: HttpUrl | None
    score: int | None


class Event(BaseModel):
    id: str
    img: HttpUrl
    series: str
    stage: str
    date: str


class Agent(BaseModel):
    title: str
    img: HttpUrl


class TeamMember(BaseModel):
    name: str
    team: str
    agents: list[Agent]
    acs: int
    kills: int
    deaths: int
    assists: int
    adr: int
    headshot_percent: int


class Round(BaseModel):
    round_number: int
    round_score: str
    winner: str
    side: str
    win_type: str


class MatchData(BaseModel):
    map: str
    teams: list[Team]
    members: list[TeamMember]


class Match(BaseModel):
    teams: list[Team]
    bans: list[str]
    event: Event
    data: list[MatchData]
    previous_encounters: list[str]
