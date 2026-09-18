from datetime import datetime

from pydantic import BaseModel, HttpUrl, computed_field, field_validator

from app import i18n
from app.constants import MatchStatus, VetoAction


class Team(BaseModel):
    name: str
    score: int | None = None


class TeamWithImage(Team):
    img: HttpUrl
    id: str | None = None


class Event(BaseModel):
    id: str
    img: HttpUrl
    series: str
    stage: str
    date: datetime | None = None
    patch: str | None = None
    status: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_label(self) -> str | None:
        return i18n.label("match_status", self.status) if self.status else None


class Agent(BaseModel):
    title: str
    img: HttpUrl


class TeamMember(BaseModel):
    id: str
    name: str
    team: str
    agents: list[Agent]
    rating: float
    acs: int
    kills: int
    deaths: int
    assists: int
    kast: int
    adr: int
    headshot_percent: int
    first_kills: int
    first_deaths: int
    first_kills_diff: int


class Round(BaseModel):
    round_number: int
    round_score: str
    winner: str
    side: str
    win_type: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def side_label(self) -> str:
        return i18n.label("side", self.side) if self.side else ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def win_type_label(self) -> str:
        return i18n.label("win_type", self.win_type)


class MatchData(BaseModel):
    map: str = ""
    teams: list[Team]
    members: list[TeamMember]
    rounds: list[Round]


class PreviousEncounters(BaseModel):
    teams: list[Team]
    match_id: str


class Video(BaseModel):
    name: str
    url: HttpUrl


class Veto(BaseModel):
    """One structured map-veto step; ``bans`` on the match keeps the raw VLR text for older clients."""

    team: str | None = None  # team tag as shown by VLR (e.g. "FNC"); None for "remains"/unknown
    action: VetoAction
    map: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def action_label(self) -> str:
        return i18n.label("veto", self.action)


class MatchVideos(BaseModel):
    streams: list[Video]
    vods: list[Video]


# Response for `GET /api/v1/matches/{match_id}`
class MatchWithDetails(BaseModel):
    teams: list[TeamWithImage]
    bans: list[str]
    veto: list[Veto] = []
    event: Event
    videos: MatchVideos
    map_count: int
    data: list[MatchData]
    previous_encounters: list[PreviousEncounters]


class MatchTeam(BaseModel):
    name: str
    id: str | None = None
    score: int | None = None


# Response for `GET /api/v1/matches`
class Match(BaseModel):
    id: str
    team1: MatchTeam
    team2: MatchTeam
    status: MatchStatus
    time: datetime
    event: str
    series: str
    event_id: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_label(self) -> str:
        return i18n.label("match_status", self.status)


class LiveSnapshot(BaseModel):
    """Store the latest match data that the live cron writes to Redis."""

    match_id: str
    version: int
    observed_at: datetime
    data: MatchWithDetails


class LiveTeam(BaseModel):
    """Store one team in a compact live update.

    :param name: The team name.
    :param img: The absolute logo URL, or ``None`` when VLR has no image.
    :param score: The current series score, or ``None`` when it is unavailable.
    """

    name: str
    img: HttpUrl | None = None
    score: int | None = None


class LiveCurrentMap(BaseModel):
    """Store the current map name and the scores for the two teams.

    ``scores`` follows the top-level ``teams`` order, not the map-card order.
    """

    name: str
    scores: list[int | None]

    @field_validator("scores")
    @classmethod
    def _require_two_scores(cls, scores: list[int | None]) -> list[int | None]:
        """Require one score for each of the two teams.

        :param scores: The map scores.
        :return: The validated scores.
        :raises ValueError: If the list does not contain exactly two scores.
        """
        if len(scores) != 2:
            raise ValueError("current_map.scores must contain exactly two values")
        return scores


class LiveMatchEvent(BaseModel):
    """Send one compact score update to a mobile Live Activity.

    :param match_id: The match ID.
    :param version: The observation ordering and deduplication token, in Unix milliseconds.
    :param observed_at: The wall-clock observation time. It is not the ordering token.
    :param terminal: Whether the match is complete.
    :param teams: The two teams in match-header order.
    :param current_map: The current map scores, or ``None`` when no map is known.
    """

    match_id: str
    version: int
    observed_at: datetime
    terminal: bool
    teams: list[LiveTeam]
    current_map: LiveCurrentMap | None = None
