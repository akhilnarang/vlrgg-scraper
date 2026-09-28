import string
import time
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, Field, HttpUrl, ValidationError, computed_field, field_validator, model_validator

from app import i18n
from app.constants import (
    LIVE_STATUSES,
    MAX_FAVORITES_PER_GROUP,
    MAX_TOKEN_LENGTH,
    VIDEO_STALE_SECONDS,
    MatchStatus,
    Platform,
    TeamSide,
    VetoAction,
    VideoPauseKind,
    VideoStatus,
)


class Team(BaseModel):
    name: str
    score: int | None = None


class TeamWithImage(Team):
    img: HttpUrl
    id: str | None = None
    tag: str | None = None  # short name, e.g. "PRX"; None until a map has been played


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
    number: int = Field(ge=1)  # 1-based series position from VLR's map-navigation slot
    map: str = ""
    teams: list[Team]
    members: list[TeamMember]
    rounds: list[Round]
    live: bool = False
    winner: str | None = None  # winning team's name once VLR marks the map as won


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


class PushCurrentMap(BaseModel):
    """Current map and its team scores."""

    name: str
    scores: list[int | None]
    number: int | None = None


# Response for `GET /api/v1/matches/{match_id}`
class MatchWithDetails(BaseModel):
    teams: list[TeamWithImage]
    bans: list[str]
    veto: list[Veto] = []
    event: Event
    videos: MatchVideos
    map_count: int
    total_maps: int = 1
    data: list[MatchData]
    previous_encounters: list[PreviousEncounters]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def current_map(self) -> PushCurrentMap | None:
        """The map being played, or up next between maps, with scores in team order; only for live matches."""
        if self.event.status not in LIVE_STATUSES or len(self.teams) != 2:
            return None
        selected = next((item for item in self.data if item.live), None)
        if selected is None:
            selected = next((item for item in self.data if item.winner is None), None)
        if selected is None:
            return None
        scores = {team.name.strip().casefold(): team.score for team in selected.teams}
        return PushCurrentMap(
            name=selected.map,
            number=selected.number,
            scores=[scores.get(team.name.strip().casefold()) for team in self.teams],
        )


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
    """Team ID, name, tag, image, and series score in a compact push state."""

    id: str | None = None
    name: str
    tag: str | None = None
    img: HttpUrl | None = None
    score: int | None = None


class PushPause(BaseModel):
    """Broadcast pause payload in compact push state.

    The state's ``observed_at`` is the pause's absolute time; the tracker's own
    relative clock is not forwarded.
    """

    kind: VideoPauseKind
    reason: str | None = None


class CompactState(BaseModel):
    """Compact match state delivered through APNs and FCM."""

    match_id: str
    observed_at: int
    terminal: bool
    total_maps: int = 1
    teams: list[PushTeam]
    current_map: PushCurrentMap | None = None
    map_winners: list[str | None] = []  # winning team ID per map; None while in progress or unplayed
    pause: PushPause | None = None  # active broadcast pause, if any

    def semantic(self) -> str:
        """Serialize state without observation time for change detection.

        :return: Stable JSON representation of the score state.
        """
        return self.model_dump_json(exclude={"observed_at"})


class VideoPause(BaseModel):
    """Broadcast pause detected by the video tracker."""

    kind: VideoPauseKind
    reason: str | None = Field(default=None, max_length=24)  # overlay reason label, e.g. GEAR
    since: int = Field(ge=0)  # the tracker's own second when the pause started; not an absolute time


class VideoTeam(BaseModel):
    """One team as the broadcast video tracker identifies it."""

    code: str = Field(max_length=16)
    name: str = Field(max_length=100)
    score: int = Field(ge=0, le=99)
    side: TeamSide | None = None  # broadcast score bar side, if reported


class VideoScore(BaseModel):
    """The latest map score read from the broadcast by the video tracker, and whether it is still reading."""

    status: VideoStatus
    observed_at: int
    map_number: int = Field(ge=1)
    teams: list[VideoTeam] = Field(min_length=2, max_length=2)
    pause: VideoPause | None = None  # active broadcast pause, if any

    @model_validator(mode="after")
    def validate_teams(self) -> Self:
        """Require one blue and one red team with distinct codes.

        :return: The validated score.
        :raises ValueError: If only one team has a side, both are the same side, or codes collide.
        """
        blue, red = self.teams[0].side, self.teams[1].side
        if (blue is None) != (red is None) or (blue is not None and blue == red):
            raise ValueError("sides must name one blue and one red team")
        if self.teams[0].code.strip().casefold() == self.teams[1].code.strip().casefold():
            raise ValueError("team codes must be distinct")
        return self

    @classmethod
    def from_cache(cls, data: bytes | None) -> VideoScore | None:
        """Parse a stored score.

        :param data: Stored JSON, or None when nothing is stored.
        :return: The score, or None when missing or no longer valid after a schema change.
        """
        try:
            return cls.model_validate_json(data) if data else None
        except ValidationError:
            return None

    def resolve_pair(self, teams: list[tuple[str, str | None]]) -> list[VideoTeam] | None:
        """Resolve two VLR teams to the two broadcast entries one-to-one.

        Each VLR team must match exactly one entry by name or tag, and neither team
        may take the other's entry. Evidence that fits both pairings is conflicting
        and rejected rather than guessed.

        :param teams: Each VLR team's name and tag, in VLR order.
        :return: The broadcast entries in the same order, or None when unmatched or ambiguous.
        """
        if len(teams) != 2:
            return None
        candidates = [self._candidates(name, tag) for name, tag in teams]
        pairings = [(first, second) for first in candidates[0] for second in candidates[1] if first != second]
        if len(pairings) != 1:
            return None
        first, second = pairings[0]
        return [self.teams[first], self.teams[second]]

    def _candidates(self, name: str, tag: str | None) -> list[int]:
        """List the broadcast entries matching one VLR team by name or tag.

        :param name: VLR team name.
        :param tag: VLR team tag, if available.
        :return: Indices of the candidate entries.
        """
        name, tag = name.strip().casefold(), (tag or "").strip().casefold()
        return [
            index
            for index, team in enumerate(self.teams)
            if team.name.strip().casefold() == name or (tag and team.code.strip().casefold() == tag)
        ]

    def same_score(self, other: VideoScore) -> bool:
        """Check whether map, scores, sides, and pause match another read.

        :param other: Score to compare against.
        :return: True if score and display state match.
        """
        if self.map_number != other.map_number or self.pause != other.pause:
            return False
        return {team.code: (team.score, team.side) for team in self.teams} == {
            team.code: (team.score, team.side) for team in other.teams
        }

    @property
    def healthy(self) -> bool:
        """Whether the tracker is reading the current map and wrote recently.

        :return: True while the video, not VLR, should drive this match's pushes.
        """
        return self.status == VideoStatus.OK and 0 <= time.time() - self.observed_at <= VIDEO_STALE_SECONDS


class VideoDelivery(BaseModel):
    """The last video score pushed to phones, and the match it was pushed for."""

    match_id: str
    video: VideoScore


class VideoContext(BaseModel):
    """Match context and ordered map names for the video tracker."""

    match_id: str
    map_number: int | None = None
    map_order: list[str]
    teams: list[str] = []  # team tags for tracker verification


VideoContextCodes = Annotated[str, Field(max_length=33, pattern=r"^(?:[^\s,]{1,16},[^\s,]{1,16})?$")]


class TokenRegistration(BaseModel):
    """Validated APNs push-to-start or FCM registration token."""

    token: str = Field(max_length=MAX_TOKEN_LENGTH, pattern=r"^[A-Za-z0-9_:-]+$")
    platform: Platform = Platform.IOS
    live_updates: bool | None = None

    @model_validator(mode="after")
    def validate_token(self) -> Self:
        """Require hexadecimal APNs tokens and normalize them to lowercase.

        :return: The registration with a normalized token.
        :raises ValueError: If an iOS token is not hexadecimal.
        """
        if self.platform == Platform.IOS:
            if len(self.token) % 2 or any(char not in string.hexdigits for char in self.token):
                raise ValueError("iOS tokens must be hexadecimal")
            self.token = self.token.lower()
        return self


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
