from pydantic import BaseModel, HttpUrl


class Team(BaseModel):
    name: str
    score: int | None


class TeamWithImage(Team):
    img: HttpUrl


class Event(BaseModel):
    id: str
    img: HttpUrl
    series: str
    stage: str
    date: str
    patch: str | None


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


class MatchWithDetails(BaseModel):
    teams: list[TeamWithImage]
    bans: list[str]
    event: Event
    data: list[MatchData]
    previous_encounters: list[str]


class MatchTeam(BaseModel):
    name: str
    score: int | None


class Match(BaseModel):
    team1: MatchTeam
    team2: MatchTeam
    status: str
    time: str | None
    id: str
    event: str
    series: str
