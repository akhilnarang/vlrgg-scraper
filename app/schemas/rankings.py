from pydantic import BaseModel, HttpUrl


class TeamRanking(BaseModel):
    name: str
    id: int
    logo: HttpUrl
    rank: int
    points: int
    country: str


class Ranking(BaseModel):
    region: str
    teams: list[TeamRanking]
