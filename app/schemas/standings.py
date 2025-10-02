from pydantic import BaseModel, HttpUrl


class TeamStanding(BaseModel):
    name: str
    id: int
    logo: HttpUrl
    rank: int
    points: int
    country: str


class CircuitStanding(BaseModel):
    region: str
    teams: list[TeamStanding]


class Standings(BaseModel):
    year: int
    circuits: list[CircuitStanding]
