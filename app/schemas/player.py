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


class Player(BaseModel):
    name: str
    alias: str
    twitch: HttpUrl | None
    twitter: str | None
    country: str
    img: HttpUrl
    agents: list[Agent]
