from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from app.enums import BracketSourceKind, BracketSourceResult, BracketStatus, MatchStatus


class BracketTeam(BaseModel):
    """A known team in a bracket slot; a TBD slot has no team object."""

    id: str  # Riot's team id
    name: str
    code: str
    img: HttpUrl | None = None
    vlr_team_id: str | None = None  # set only when a VLR team is matched to this Riot team


class BracketSource(BaseModel):
    """The edge a slot's participant arrives on: a match winner/loser, or a seed."""

    kind: BracketSourceKind
    match_id: str | None = None  # Riot id of the feeding match when kind is match
    result: BracketSourceResult | None = None
    seed: int | None = None


class BracketSlot(BaseModel):
    """One side of a bracket match, in Riot's display order."""

    team: BracketTeam | None = None  # None is TBD (Riot team id "0")
    source: BracketSource | None = None
    score: int | None = None  # series game wins; None while upcoming or TBD
    winner: bool | None = None  # None until the match is completed or live


class BracketMatch(BaseModel):
    """One Riot series in Riot's layout, with the VLR match it is linked to when known."""

    id: str
    vlr_match_id: str | None = None
    label: str | None = None
    status: MatchStatus
    start_time: datetime | None = None
    best_of: int | None = None
    slots: list[BracketSlot] = Field(min_length=2, max_length=2)


class BracketCell(BaseModel):
    """A display group; it is not a round, and can hold matches from more than one."""

    name: str
    matches: list[BracketMatch]


class BracketColumn(BaseModel):
    """One left-to-right layout layer as Riot draws it."""

    cells: list[BracketCell]


class BracketSection(BaseModel):
    """One ``type: bracket`` section, preserving Riot's column, cell and match order."""

    id: str
    tournament: str
    stage: str
    name: str
    columns: list[BracketColumn]


class EventBrackets(BaseModel):
    """Response of ``GET /events/{id}/brackets``.

    ``sections`` stays the only field a client must read: empty is valid and the older meaning
    ("nothing to show") is unchanged. ``available`` and ``status`` say why it is empty, and
    ``fetched_at`` says how old the served layouts are.
    """

    event_id: str
    sections: list[BracketSection] = []
    available: bool = False
    status: BracketStatus = BracketStatus.UNAVAILABLE
    fetched_at: datetime | None = None  # oldest layout the returned sections came from
