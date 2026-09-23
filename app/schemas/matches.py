from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, computed_field, field_validator

from app import i18n
from app.constants import MAX_FAVORITES_PER_GROUP, MAX_TOKEN_LENGTH, MatchStatus, VetoAction


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


class PushTeam(BaseModel):
    """Team name, image, and series score in a compact push state."""

    name: str
    img: HttpUrl | None = None
    score: int | None = None


class PushCurrentMap(BaseModel):
    """Current map and its team scores."""

    name: str
    scores: list[int | None]


class CompactState(BaseModel):
    """Compact match state delivered through APNs and FCM."""

    match_id: str
    observed_at: int
    terminal: bool
    teams: list[PushTeam]
    current_map: PushCurrentMap | None = None

    def semantic(self) -> str:
        """Serialize state without observation time for change detection.

        :return: Stable JSON representation of the score state.
        """
        return self.model_dump_json(exclude={"observed_at"})


class TokenRegistration(BaseModel):
    """Validated APNs push-to-start token registration."""

    token: str = Field(max_length=MAX_TOKEN_LENGTH, pattern=r"^(?:[0-9a-fA-F]{2})+$")

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        """Normalize an APNs token to lowercase hexadecimal.

        :param value: Validated hexadecimal token.
        :return: Lowercase token.
        """
        return value.lower()


class Favorites(BaseModel):
    """Client favorites grouped by entity type."""

    teams: list[str] = Field(default_factory=list, max_length=MAX_FAVORITES_PER_GROUP)
    matches: list[str] = Field(default_factory=list, max_length=MAX_FAVORITES_PER_GROUP)
    players: list[str] = Field(default_factory=list, max_length=MAX_FAVORITES_PER_GROUP)
    events: list[str] = Field(default_factory=list, max_length=MAX_FAVORITES_PER_GROUP)

    @field_validator("teams", "matches", "players", "events")
    @classmethod
    def validate_ids(cls, values: list[str]) -> list[str]:
        """Reject nonnumeric or out-of-range favorite IDs.

        :param values: IDs for a favorite group.
        :return: Validated IDs.
        :raises ValueError: If an ID is not a positive ASCII integer.
        """
        if any(not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 9_999_999_999 for value in values):
            raise ValueError("favorite IDs must be positive ASCII digits")
        return values
