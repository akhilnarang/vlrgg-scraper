"""Riot web persisted-service payloads, validated at the boundary, and the shapes we store.

Timestamps are ``AwareDatetime``: Riot sends a zone, and a naive value would otherwise raise
later when the linker subtracts it from VLR's aware time or brackets sort two tournaments. A
naive timestamp rejects its row, which the readers log and skip, rather than the whole payload.
"""

import logging
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator
from pydantic.alias_generators import to_camel

from app.enums import RiotGameState, RiotMatchState, VodProvider

logger = logging.getLogger(__name__)


class RiotWebPayload(BaseModel):
    """Reads Riot's camelCase fields and ignores the rest of its payload."""

    model_config = ConfigDict(alias_generator=to_camel)


class TeamResult(RiotWebPayload):
    game_wins: int = 0


class ScheduleTeam(RiotWebPayload):
    name: str


class ScheduleStrategy(RiotWebPayload):
    count: int


class ScheduleMatch(RiotWebPayload):
    id: str = Field(pattern=r"^\d{1,20}$")
    teams: list[ScheduleTeam] = Field(min_length=2, max_length=2)
    strategy: ScheduleStrategy | None = None


class ScheduleLeague(RiotWebPayload):
    name: str


class ScheduleEvent(RiotWebPayload):
    start_time: AwareDatetime
    league: ScheduleLeague
    match: ScheduleMatch | None = None


class LiveMatch(RiotWebPayload):
    """The little Riot's live list promises about a series. No populated list has been captured."""

    id: str = Field(pattern=r"^\d{1,20}$")
    teams: list[ScheduleTeam] = []


class LiveEvent(RiotWebPayload):
    """One series Riot's site shows live. Only ``match.id`` is required, so an unseen shape still reads."""

    start_time: AwareDatetime | None = None
    state: RiotMatchState | None = None
    league: ScheduleLeague | None = None
    match: LiveMatch | None = None


class SchedulePage(BaseModel):
    """One page of Riot's schedule; ``older`` is the opaque cursor for the next page."""

    events: list[ScheduleEvent]
    older: str | None = None


class SchedulePages(RiotWebPayload):
    older: str | None = None


class ScheduleBody(RiotWebPayload):
    # ``Any`` because a ``null`` entry must reach the per-event parser: one bad event costs one row.
    events: list[Any]
    pages: SchedulePages | None = None


class ScheduleData(RiotWebPayload):
    schedule: ScheduleBody


class ScheduleEnvelope(RiotWebPayload):
    data: ScheduleData


class StoredTeam(BaseModel):
    """A schedule team as stored in ``riot_web_matches.teams``."""

    name: str


class DetailsVod(RiotWebPayload):
    """A VOD segment of one game; ``endMillis`` is deliberately not modelled, it is unreliable."""

    parameter: str
    locale: str
    provider: str
    start_millis: int | None = None


class DetailsGame(RiotWebPayload):
    number: int = Field(ge=1)
    state: RiotGameState
    vods: list[DetailsVod] = []


class DetailsTeam(RiotWebPayload):
    name: str
    result: TeamResult | None = None


class DetailsMatch(RiotWebPayload):
    teams: list[DetailsTeam] = Field(min_length=2, max_length=2)
    games: list[DetailsGame] = []

    @field_validator("games", mode="before")
    @classmethod
    def _readable_games(cls, games: object) -> object:
        """Keep the games that validate, so one bad game costs one map, not the series.

        :param games: Riot's raw game list.
        :return: The readable games, in Riot's order.
        """
        if not isinstance(games, list):
            return games
        readable = []
        for game in games:
            try:
                readable.append(DetailsGame.model_validate(game))
            except ValidationError:
                logger.warning("skipping an unreadable Riot web details game", exc_info=True)
        return readable


class DetailsTournament(RiotWebPayload):
    id: str = Field(pattern=r"^\d{1,20}$")


class EventDetails(RiotWebPayload):
    tournament: DetailsTournament
    match: DetailsMatch | None = None


class EventData(RiotWebPayload):
    event: EventDetails | None  # ``event: null`` for ids Riot no longer serves


class EventEnvelope(RiotWebPayload):
    data: EventData


class StoredVod(BaseModel):
    """A VOD offset kept in ``riot_web_matches.details``; ``start_ms`` is the only bound we trust."""

    provider: VodProvider
    locale: str
    parameter: str
    start_ms: int = Field(ge=0)


class StoredGame(BaseModel):
    """One game of a series as stored, with the offset VODs that carry it."""

    number: int = Field(ge=1)
    state: RiotGameState
    vods: list[StoredVod] = []


class StoredDetails(BaseModel):
    """Riot's ``getEventDetails`` as we store it; the games are ours, and each score keeps its team."""

    tournament_id: str
    game_wins: dict[str, int] = {}  # Riot team name -> games won, so a reversed score stays wrong
    games: list[StoredGame] = []


class StandingsOrigin(RiotWebPayload):
    """Where a bracket slot's participant entered from; ``slot`` 1 wins and 2 loses a match origin."""

    structural_id: str
    type: str
    slot: int


class StandingsTeamResult(RiotWebPayload):
    outcome: str | None = None
    game_wins: int = 0


class StandingsTeam(RiotWebPayload):
    id: str = Field(pattern=r"^\d{1,20}$")
    name: str
    code: str
    image: HttpUrl | None = None
    result: StandingsTeamResult | None = None
    origin: StandingsOrigin | None = None


class StandingsMatch(RiotWebPayload):
    id: str = Field(pattern=r"^\d{1,20}$")
    structural_id: str
    state: str
    description: str | None = None
    teams: list[StandingsTeam] = Field(min_length=2, max_length=2)


class StandingsCell(RiotWebPayload):
    name: str
    matches: list[StandingsMatch]


class StandingsColumn(RiotWebPayload):
    cells: list[StandingsCell]


class StandingsSection(RiotWebPayload):
    id: str
    name: str
    type: str
    columns: list[StandingsColumn]


class StandingsStage(RiotWebPayload):
    name: str
    sections: list[StandingsSection]

    @field_validator("sections", mode="before")
    @classmethod
    def _readable_sections(cls, sections: object) -> object:
        """Keep the sections that validate, so one malformed section costs only itself.

        :param sections: Riot's raw section list.
        :return: The readable sections, in Riot's order.
        """
        if not isinstance(sections, list):
            return sections
        readable = []
        for section in sections:
            try:
                readable.append(StandingsSection.model_validate(section))
            except ValidationError:
                logger.warning("skipping an unreadable Riot web standings section", exc_info=True)
        return readable


class Tournament(RiotWebPayload):
    """One ``data.standings[]`` entry: Riot's stages and bracket layout, validated for conversion."""

    name: str
    start_time: AwareDatetime | None = None
    stages: list[StandingsStage]


class StandingsBody(RiotWebPayload):
    standings: list[dict] = []  # validated one at a time; an unknown id answers with none


class StandingsEnvelope(RiotWebPayload):
    data: StandingsBody


class NamePair(BaseModel):
    """The normalised name keys of one series' two teams, matched in either order."""

    first: frozenset[str]
    second: frozenset[str]

    def matches(self, other: NamePair) -> bool:
        """Check whether two series name the same two teams, in either order.

        :param other: The other series' name keys.
        :return: True when both teams meet across the two pairs.
        """
        same_order = bool(self.first & other.first) and bool(self.second & other.second)
        crossed = bool(self.first & other.second) and bool(self.second & other.first)
        return same_order or crossed
