"""Riot's esports match API payloads, and the shapes that link its games to VLR maps."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

VlrTeamSlot = Literal["team1", "team2"]


class _RiotPayload(BaseModel):
    """Reads Riot's camelCase fields and ignores the rest of its payload."""

    model_config = ConfigDict(alias_generator=to_camel)


class RecentGames(_RiotPayload):
    match_ids: list[str]


class RiotMatchInfo(_RiotPayload):
    map_id: str
    is_completed: bool
    game_start_millis: int


class RiotPlayer(_RiotPayload):
    game_name: str
    tag_line: str
    team_id: str


class RiotTeam(_RiotPayload):
    team_id: str
    rounds_won: int = 0


class RiotGame(_RiotPayload):
    match_info: RiotMatchInfo
    players: list[RiotPlayer]
    teams: list[RiotTeam]
    round_results: list[dict] = Field(default_factory=list)


class Side(BaseModel):
    """One side of a Riot game."""

    rounds_won: int
    players: list[str]


class Game(BaseModel):
    """A completed Riot game, with whatever linking could read from it."""

    riot_id: str
    map: str | None
    started_at: datetime
    red: Side | None
    blue: Side | None
    data: dict

    @property
    def linkable(self) -> bool:
        """Whether linking has a known map and both sides' players to compare.

        :return: True when the game can be compared with a VLR map.
        """
        return (
            self.map is not None
            and self.red is not None
            and self.blue is not None
            and bool(self.red.players)
            and bool(self.blue.players)
        )


class VlrTeam(BaseModel):
    """A team on one VLR map."""

    name: str
    tag: str | None
    map_score: int
    players: list[str]


class Candidate(BaseModel):
    """A VLR map whose name and final score fit a Riot game; teams are in the map panel's order."""

    match_id: str
    map_number: int
    team1: VlrTeam
    team2: VlrTeam


class GameLink(BaseModel):
    """The model's verdict on whether a Riot game is a VLR map, and which side is which team."""

    red_team: VlrTeamSlot | Literal["neither"]
    blue_team: VlrTeamSlot | Literal["neither"]
    same_game: bool
    confidence: float = Field(ge=0, le=1)
    reason: str
