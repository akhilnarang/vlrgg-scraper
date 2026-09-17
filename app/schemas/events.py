from datetime import date

from pydantic import BaseModel, HttpUrl, computed_field

from app import i18n
from app.constants import EventStatus, MatchStatus


# Response for `GET /api/v1/events`
class Event(BaseModel):
    id: str
    title: str
    status: EventStatus
    prize: str
    dates: str
    location: str
    img: HttpUrl

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_label(self) -> str:
        return i18n.label("event_status", self.status)


class PrizeTeam(BaseModel):
    id: str
    name: str
    img: HttpUrl
    country: str


class Prize(BaseModel):
    position: str
    prize: str
    team: PrizeTeam | None = None


class Player(BaseModel):
    id: str
    name: str
    country: str


class Team(BaseModel):
    name: str
    id: str
    img: HttpUrl
    seed: str | None = None
    # roster: list[Player]


class MatchTeam(BaseModel):
    name: str
    region: str
    score: int | None = None


class Match(BaseModel):
    id: str
    time: str
    date: date
    eta: str | None = None
    status: MatchStatus
    teams: list[MatchTeam]
    round: str
    stage: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_label(self) -> str:
        return i18n.label("match_status", self.status)


class EventStandings(BaseModel):
    logo: HttpUrl
    team: str
    country: str
    wins: int
    losses: int
    ties: int
    map_difference: int
    round_difference: int
    round_delta: int
    group: str | None = None


# Response for `GET /api/v1/events/{id}`
class EventWithDetails(BaseModel):
    id: str
    title: str
    subtitle: str
    dates: str
    prize: str
    location: str
    status: EventStatus
    img: HttpUrl
    prizes: list[Prize] = []
    teams: list[Team] = []
    matches: list[Match]
    standings: list[EventStandings] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_label(self) -> str:
        return i18n.label("event_status", self.status)
