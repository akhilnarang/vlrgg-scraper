from pydantic import BaseModel, HttpUrl


class Agent(BaseModel):
    name: str
    img: HttpUrl
    count: int
    percent: float
    rounds: int
    rating: float
    acs: float
    kd: float
    adr: float
    kast: float
    kpr: float
    apr: float
    fkpr: float
    fdpr: float
    k: int
    d: int
    a: int
    fk: int
    fd: int


class PlayerTeam(BaseModel):
    id: str
    name: str
    img: HttpUrl


# Response for `GET /api/v1/player/{id}`
class Player(BaseModel):
    name: str
    alias: str
    twitch: HttpUrl | None = None
    twitter: str | None = None
    country: str
    img: HttpUrl
    agents: list[Agent]
    total_winnings: float = 0.0
    current_team: PlayerTeam | None = None
    past_teams: list[PlayerTeam] = []
