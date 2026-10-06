"""SQLAlchemy models for live-update subscriptions and the team ranking ledger."""

from datetime import date

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.constants import ELO_BASE, Circuit, Platform, RankingScope
from app.db.types import JSONB


class Base(DeclarativeBase):
    """Base metadata for live-update tables."""


class Client(Base):
    """Client identified by its UUID."""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    live_updates: Mapped[bool | None] = mapped_column(nullable=True)


class DeviceToken(Base):
    """APNs push-to-start or FCM registration token belonging to a client."""

    __tablename__ = "device_tokens"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(String(4096))
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, length=16, values_callable=lambda enum: [member.value for member in enum]),
        server_default=Platform.IOS.value,
    )


class Favorite(Base):
    """One client favorite for a match, team, player, or event."""

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("client_id", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16), index=True)
    entity_id: Mapped[str] = mapped_column(String(10), index=True)


class MatchPushState(Base):
    """Stored broadcast channel and last compact state for a match."""

    __tablename__ = "match_push_states"

    match_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class LiveActivityStart(Base):
    """Record that a client received a start attempt for a match."""

    __tablename__ = "live_activity_starts"
    __table_args__ = (UniqueConstraint("client_id", "match_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    match_id: Mapped[str] = mapped_column(String(10), index=True)


class Player(Base):
    """Last scraped VLR player page, kept to resolve a player's current team."""

    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    first_seen_at: Mapped[int] = mapped_column()
    last_fetched_at: Mapped[int] = mapped_column()


class Team(Base):
    """A VLR team as last seen by any scrape, plus the Riot esports fields it is matched to."""

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank: Mapped[int | None] = mapped_column(nullable=True)
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    riot_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    riot_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    riot_logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[int] = mapped_column()
    last_fetched_at: Mapped[int] = mapped_column()


class IdMapping(Base):
    """A simplified team or event name and the VLR ID it resolves to, as the Redis ID map holds it."""

    __tablename__ = "id_map"

    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(String(10))


class EventRecord(Base):
    """A VLR event whose matches are in the ranking ledger."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    circuit: Mapped[Circuit] = mapped_column(
        Enum(Circuit, native_enum=False, length=16, values_callable=lambda enum: [member.value for member in enum]),
        server_default=Circuit.OTHER.value,
        index=True,
    )
    circuit_checked_at: Mapped[int | None] = mapped_column(nullable=True)


class MatchRecord(Base):
    """A completed match in the ranking ledger; team IDs are NULL when VLR shows TBD."""

    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    played_on: Mapped[date] = mapped_column(index=True)
    team_a_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team_b_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team_a_score: Mapped[int | None] = mapped_column(nullable=True)
    team_b_score: Mapped[int | None] = mapped_column(nullable=True)
    patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    ingested_at: Mapped[int] = mapped_column()


class MapRecord(Base):
    """One played map of a match, oriented to the match's team order."""

    __tablename__ = "maps"

    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    map_index: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    team_a_score: Mapped[int] = mapped_column()
    team_b_score: Mapped[int] = mapped_column()


class RankingResult(Base):
    """One Elo-moving result: a series or a played map, with the ratings before it."""

    __tablename__ = "ranking_results"
    __table_args__ = (
        UniqueConstraint("match_id", "scope", "map_index"),
        Index("ix_ranking_results_team_a_scope_seq", "team_a_id", "scope", "seq"),
        Index("ix_ranking_results_team_b_scope_seq", "team_b_id", "scope", "seq"),
    )

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"))
    scope: Mapped[RankingScope] = mapped_column(
        Enum(RankingScope, native_enum=False, length=8, values_callable=lambda enum: [member.value for member in enum])
    )
    map_index: Mapped[int] = mapped_column()
    played_on: Mapped[date] = mapped_column()
    team_a_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    team_b_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    winner_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    team_a_elo_before: Mapped[float] = mapped_column()
    team_b_elo_before: Mapped[float] = mapped_column()
    team_a_delta: Mapped[float] = mapped_column()


class TeamElo(Base):
    """A team's stored Elo per scope; ratings never decay, so no date marker is kept."""

    __tablename__ = "team_elo"

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    match_elo: Mapped[float] = mapped_column(default=ELO_BASE)
    map_elo: Mapped[float] = mapped_column(default=ELO_BASE)
    matches: Mapped[int] = mapped_column(default=0)
    match_wins: Mapped[int] = mapped_column(default=0)
    maps: Mapped[int] = mapped_column(default=0)
    map_wins: Mapped[int] = mapped_column(default=0)
    first_played_on: Mapped[date | None] = mapped_column(nullable=True)
    last_played_on: Mapped[date | None] = mapped_column(nullable=True)


class TeamCircuit(Base):
    """How many rated matches a team played in one circuit, and when it last did."""

    __tablename__ = "team_circuits"

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    circuit: Mapped[Circuit] = mapped_column(
        Enum(Circuit, native_enum=False, length=16, values_callable=lambda enum: [member.value for member in enum]),
        primary_key=True,
    )
    matches: Mapped[int] = mapped_column()
    last_played_on: Mapped[date] = mapped_column()
