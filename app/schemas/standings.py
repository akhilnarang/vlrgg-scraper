from pydantic import BaseModel, HttpUrl, computed_field

from app import i18n


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def region_label(self) -> str:
        return i18n.label("region", self.region)


class Standings(BaseModel):
    year: int
    circuits: list[CircuitStanding]
