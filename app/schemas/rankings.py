from pydantic import BaseModel, HttpUrl, computed_field

from app import i18n


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def region_label(self) -> str:
        return i18n.label("region", self.region)

    teams: list[TeamRanking]
