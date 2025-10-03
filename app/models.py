from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, JSON, Float, Table, Index
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# Association table for many-to-many relationship between Event and Team
event_teams = Table(
    "event_teams",
    Base.metadata,
    Column("event_id", String, ForeignKey("events.id"), primary_key=True),
    Column("team_id", String, ForeignKey("teams.id"), primary_key=True),
)
"""Association table linking events to teams in a many-to-many relationship."""


class Team(Base):
    """Model representing a Valorant esports team.

    Attributes:
        id: Unique identifier for the team.
        name: Full name of the team.
        normalized_name: Normalized version of the name for unique indexing and Redis keys.
        tag: Team tag or abbreviation.
        img: URL to the team's logo image.
        website: Official website URL.
        twitter: Twitter handle or URL.
        country: Country of origin.
        rank: Current world ranking.
        region: Competitive region.
        players: Relationship to Player models (one-to-many).
        events: Relationship to Event models (many-to-many).
    """

    __tablename__ = "teams"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    normalized_name = Column(String, unique=True, nullable=False)
    tag = Column(String)
    img = Column(String)
    website = Column(String)
    twitter = Column(String)
    country = Column(String)
    rank = Column(Integer)
    region = Column(String)

    # Relationships
    players = relationship("Player", back_populates="team")
    events = relationship("Event", secondary=event_teams, back_populates="teams")


class Player(Base):
    """Model representing a Valorant player.

    Attributes:
        id: Unique identifier for the player.
        name: Real name of the player.
        alias: In-game alias or nickname.
        role: Player's role (e.g., Duelist, Controller).
        player_type: Type of player (e.g., player, coach, manager).
        img: URL to the player's profile image.
        team_id: Foreign key to the player's current team.
        twitch: Twitch streaming URL.
        twitter: Twitter handle or URL.
        country: Country of origin.
        total_winnings: Total prize money won.
        team: Relationship to Team model (many-to-one).
    """

    __tablename__ = "players"

    id = Column(String, primary_key=True)
    name = Column(String)
    alias = Column(String, nullable=False)
    role = Column(String)
    player_type = Column(String, default="player")
    img = Column(String)
    team_id = Column(String, ForeignKey("teams.id"))
    twitch = Column(String)
    twitter = Column(String)
    country = Column(String)
    total_winnings = Column(Float, default=0.0)

    # Relationships
    team = relationship("Team", back_populates="players")


class Match(Base):
    """Model representing a Valorant match.

    Attributes:
        id: Unique identifier for the match.
        team_a_id: Foreign key to team A.
        team_b_id: Foreign key to team B.
        event_id: Foreign key to the event this match belongs to.
        status: Current status of the match (e.g., upcoming, live, completed).
        time: Scheduled or actual time of the match.
        series: Series information (e.g., best-of-3).
        event_name: Name of the event.
        team_a_players: JSON list of player IDs for team A.
        team_b_players: JSON list of player IDs for team B.
        team_a: Relationship to Team model for team A.
        team_b: Relationship to Team model for team B.
        event: Relationship to Event model.
    """

    __tablename__ = "matches"

    id = Column(String, primary_key=True)
    team_a_id = Column(String, ForeignKey("teams.id"))
    team_b_id = Column(String, ForeignKey("teams.id"))
    event_id = Column(String, ForeignKey("events.id"))
    status = Column(String)
    time = Column(DateTime)
    series = Column(String)
    event_name = Column(String)
    team_a_players = Column(JSON)  # List of player IDs for team A
    team_b_players = Column(JSON)  # List of player IDs for team B

    # Relationships
    team_a = relationship("Team", foreign_keys=[team_a_id])
    team_b = relationship("Team", foreign_keys=[team_b_id])
    event = relationship("Event", back_populates="matches")


class Event(Base):
    """Model representing a Valorant esports event or tournament.

    Attributes:
        id: Unique identifier for the event.
        title: Full title of the event.
        status: Current status (e.g., upcoming, ongoing, completed).
        prize: Total prize pool.
        dates: Event dates.
        location: Event location.
        img: URL to the event's banner image.
        matches: Relationship to Match models (one-to-many).
        teams: Relationship to Team models (many-to-many).
    """

    __tablename__ = "events"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    status = Column(String)
    prize = Column(String)
    dates = Column(String)
    location = Column(String)
    img = Column(String)

    # Relationships
    matches = relationship("Match", back_populates="event")
    teams = relationship("Team", secondary=event_teams, back_populates="events")


# Indexes for performance
Index("idx_team_normalized_name", Team.normalized_name)
Index("idx_player_alias", Player.alias)
Index("idx_player_team_id", Player.team_id)
Index("idx_match_time", Match.time)
Index("idx_match_event_id", Match.event_id)
Index("idx_match_team_a_id", Match.team_a_id)
Index("idx_match_team_b_id", Match.team_b_id)
Index("idx_event_title", Event.title)
