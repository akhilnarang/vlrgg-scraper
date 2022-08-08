from pydantic import BaseModel, HttpUrl


class TeamRanking(BaseModel):
    name: str
    id: int
    logo: HttpUrl
    rank: int


class Ranking(BaseModel):
    region: str
    teams: list[TeamRanking]
