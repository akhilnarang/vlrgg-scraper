"""Response and query models for the Elo team rankings API."""

from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.constants import ID_REGEX, RANKING_MIN_MATCHES, Circuit, RankingOrder, RankingSort, Region

EntityId = Annotated[str, Field(pattern=ID_REGEX)]


class RankingQuery(BaseModel):
    """Validated query for the ranked team list."""

    circuit: Circuit | Literal["all"] | None = Field(
        default=None, description='None or "all" ranks every circuit together'
    )
    region: Region | Literal["all"] | None = Field(
        default=None, description='None or "all" ranks every region together'
    )
    min_matches: int = Field(default=RANKING_MIN_MATCHES, ge=0, le=1000)
    include_inactive: bool = False
    sort: RankingSort = RankingSort.ELO
    order: RankingOrder = RankingOrder.DESC
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @field_validator("circuit", "region", mode="before")
    @classmethod
    def normalize_filters(cls, value: Any) -> Any:
        """Allow 'all' and case-insensitive circuit and region names."""
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("", "all"):
                return None
            return lowered
        return value


class PredictQuery(BaseModel):
    """Validated query for a head-to-head prediction."""

    team_a: EntityId
    team_b: EntityId

    @model_validator(mode="after")
    def validate_distinct_teams(self) -> Self:
        """Reject a match of a team against itself.

        :return: The validated query.
        :raises ValueError: If both sides name the same team.
        """
        if self.team_a == self.team_b:
            raise ValueError("team_a and team_b must be different teams")
        return self


class TeamSummary(BaseModel):
    """Team identity shared by the ranking responses."""

    id: str
    name: str
    tag: str | None = None
    logo: str | None = None
    country: str | None = None
    region: str | None = None


class Stats(BaseModel):
    """Win-loss record for one scope."""

    played: int
    wins: int
    losses: int
    win_rate: float


class TeamRankingItem(BaseModel):
    """One team's line in the ranked list."""

    rank: int
    overall_rank: int
    team: TeamSummary
    elo: int
    map_elo: int
    matches: Stats
    maps: Stats
    last_played_on: date | None
    primary_circuit: Circuit | None
    circuits: list[Circuit]
    region: Region | None = None


class RankingListResponse(BaseModel):
    """Paginated team ranking for one circuit and region."""

    as_of: date
    algorithm: str
    circuit: Circuit | Literal["all"] = Field(description='The requested circuit, or "all"')
    region: Region | Literal["all"] = Field(default="all", description='The requested region, or "all"')
    total: int
    limit: int
    offset: int
    teams: list[TeamRankingItem]


class TeamCircuitSummary(BaseModel):
    """A team's activity in one circuit."""

    circuit: Circuit
    matches: int
    last_played_on: date


class RecentMatchItem(BaseModel):
    """One recent series for a team's profile."""

    match_id: str
    played_on: date
    event: str
    stage: str | None = None
    opponent: TeamSummary
    team_score: int | None = None
    opponent_score: int | None = None
    won: bool


class TeamRankingProfileResponse(BaseModel):
    """A team's Elo, ranks, circuit activity, form, and recent series."""

    team: TeamSummary
    rank: int | None
    circuit_rank: int | None
    region: Region | None = None
    region_rank: int | None = None
    elo: int
    map_elo: int
    matches: Stats
    maps: Stats
    first_played_on: date | None
    last_played_on: date | None
    active: bool
    circuits: list[TeamCircuitSummary]
    form: str
    recent: list[RecentMatchItem]


class WinProbabilities(BaseModel):
    """Complementary win probabilities for the two sides."""

    team_a: float
    team_b: float


class TeamEloSummary(TeamSummary):
    """Team identity plus the ratings a prediction uses."""

    elo: int
    map_elo: int
    matches: Stats
    maps: Stats
    last_played_on: date | None


class HeadToHeadSummary(BaseModel):
    """Series and map history between the two predicted teams."""

    matches: int
    team_a_wins: int
    team_b_wins: int
    maps: int
    team_a_map_wins: int
    team_b_map_wins: int
    last_played_on: date | None


class PredictResponse(BaseModel):
    """Predicted match and map win probabilities plus head-to-head history."""

    as_of: date
    team_a: TeamEloSummary
    team_b: TeamEloSummary
    match: WinProbabilities
    map: WinProbabilities
    head_to_head: HeadToHeadSummary
