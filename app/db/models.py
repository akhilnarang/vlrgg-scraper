"""Minimal SQLAlchemy models for live-update subscriptions."""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata for live-update tables."""


class Client(Base):
    """Client identified by its UUID."""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)


class DeviceToken(Base):
    """APNs push-to-start token belonging to a client."""

    __tablename__ = "device_tokens"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(String(4096))


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
