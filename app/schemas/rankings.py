from pydantic import BaseModel, HttpUrl


class TeamRanking(BaseModel):
    name: str
    id: int
    logo: HttpUrl
    rank: int
    points: int
    country: str


# Response for `GET /api/v1/rankings`
class Ranking(BaseModel):
    region: str
    teams: list[TeamRanking]
