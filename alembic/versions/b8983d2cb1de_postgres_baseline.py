"""PostgreSQL baseline schema, replacing the SQLite-era migration chain."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b8983d2cb1de"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create every table.

    :return: None.
    """
    op.create_table(
        "clients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("live_updates", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=10), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "circuit",
            sa.Enum(
                "vct",
                "vcl",
                "t3",
                "gc",
                "collegiate",
                "offseason",
                "other",
                name="circuit",
                native_enum=False,
                length=16,
            ),
            server_default="other",
            nullable=False,
        ),
        sa.Column("circuit_checked_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_circuit"), "events", ["circuit"], unique=False)
    op.create_table(
        "id_map",
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("kind", "key"),
    )
    op.create_table(
        "live_matches",
        sa.Column("match_id", sa.String(length=10), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("detail_fetched_at", sa.Integer(), nullable=True),
        sa.Column("video", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("video_received_at", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_table(
        "match_push_states",
        sa.Column("match_id", sa.String(length=10), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("last_state_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_table(
        "players",
        sa.Column("id", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("team_id", sa.String(length=10), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_seen_at", sa.Integer(), nullable=False),
        sa.Column("last_fetched_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("tag", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("logo", sa.Text(), nullable=True),
        sa.Column("riot_code", sa.Text(), nullable=True),
        sa.Column("riot_name", sa.Text(), nullable=True),
        sa.Column("riot_logo", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.Integer(), nullable=False),
        sa.Column("last_fetched_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "device_tokens",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("token", sa.String(length=4096), nullable=False),
        sa.Column(
            "platform",
            sa.Enum("iOS", "android", name="platform", native_enum=False, length=16),
            server_default="iOS",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_table(
        "favorites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "entity_type", "entity_id"),
    )
    op.create_index(op.f("ix_favorites_client_id"), "favorites", ["client_id"], unique=False)
    op.create_index(op.f("ix_favorites_entity_id"), "favorites", ["entity_id"], unique=False)
    op.create_index(op.f("ix_favorites_entity_type"), "favorites", ["entity_type"], unique=False)
    op.create_table(
        "live_activity_starts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("match_id", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "match_id"),
    )
    op.create_index(op.f("ix_live_activity_starts_client_id"), "live_activity_starts", ["client_id"], unique=False)
    op.create_index(op.f("ix_live_activity_starts_match_id"), "live_activity_starts", ["match_id"], unique=False)
    op.create_table(
        "matches",
        sa.Column("id", sa.String(length=10), nullable=False),
        sa.Column("event_id", sa.String(length=10), nullable=False),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("played_on", sa.Date(), nullable=False),
        sa.Column("team_a_id", sa.String(length=10), nullable=True),
        sa.Column("team_b_id", sa.String(length=10), nullable=True),
        sa.Column("team_a_score", sa.Integer(), nullable=True),
        sa.Column("team_b_score", sa.Integer(), nullable=True),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("ingested_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_a_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_b_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_matches_event_id"), "matches", ["event_id"], unique=False)
    op.create_index(op.f("ix_matches_played_on"), "matches", ["played_on"], unique=False)
    op.create_table(
        "team_circuits",
        sa.Column("team_id", sa.String(length=10), nullable=False),
        sa.Column(
            "circuit",
            sa.Enum(
                "vct",
                "vcl",
                "t3",
                "gc",
                "collegiate",
                "offseason",
                "other",
                name="circuit",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("matches", sa.Integer(), nullable=False),
        sa.Column("last_played_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("team_id", "circuit"),
    )
    op.create_table(
        "team_elo",
        sa.Column("team_id", sa.String(length=10), nullable=False),
        sa.Column("match_elo", sa.Double(), nullable=False),
        sa.Column("map_elo", sa.Double(), nullable=False),
        sa.Column("matches", sa.Integer(), nullable=False),
        sa.Column("match_wins", sa.Integer(), nullable=False),
        sa.Column("maps", sa.Integer(), nullable=False),
        sa.Column("map_wins", sa.Integer(), nullable=False),
        sa.Column("first_played_on", sa.Date(), nullable=True),
        sa.Column("last_played_on", sa.Date(), nullable=True),
        sa.Column(
            "region",
            sa.Enum("americas", "emea", "pacific", "china", name="region", native_enum=False, length=16),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("team_id"),
    )
    op.create_table(
        "maps",
        sa.Column("match_id", sa.String(length=10), nullable=False),
        sa.Column("map_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("team_a_score", sa.Integer(), nullable=False),
        sa.Column("team_b_score", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
        ),
        sa.PrimaryKeyConstraint("match_id", "map_index"),
    )
    op.create_table(
        "ranking_results",
        sa.Column("seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.String(length=10), nullable=False),
        sa.Column("scope", sa.Enum("match", "map", name="rankingscope", native_enum=False, length=8), nullable=False),
        sa.Column("map_index", sa.Integer(), nullable=False),
        sa.Column("played_on", sa.Date(), nullable=False),
        sa.Column("team_a_id", sa.String(length=10), nullable=False),
        sa.Column("team_b_id", sa.String(length=10), nullable=False),
        sa.Column("winner_team_id", sa.String(length=10), nullable=False),
        sa.Column("team_a_elo_before", sa.Double(), nullable=False),
        sa.Column("team_b_elo_before", sa.Double(), nullable=False),
        sa.Column("team_a_delta", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_a_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_b_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["winner_team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("match_id", "scope", "map_index"),
    )
    op.create_index(
        "ix_ranking_results_team_a_scope_seq", "ranking_results", ["team_a_id", "scope", "seq"], unique=False
    )
    op.create_index(
        "ix_ranking_results_team_b_scope_seq", "ranking_results", ["team_b_id", "scope", "seq"], unique=False
    )


def downgrade() -> None:
    """Drop every table.

    :return: None.
    """
    op.drop_index("ix_ranking_results_team_b_scope_seq", table_name="ranking_results")
    op.drop_index("ix_ranking_results_team_a_scope_seq", table_name="ranking_results")
    op.drop_table("ranking_results")
    op.drop_table("maps")
    op.drop_table("team_elo")
    op.drop_table("team_circuits")
    op.drop_index(op.f("ix_matches_played_on"), table_name="matches")
    op.drop_index(op.f("ix_matches_event_id"), table_name="matches")
    op.drop_table("matches")
    op.drop_index(op.f("ix_live_activity_starts_match_id"), table_name="live_activity_starts")
    op.drop_index(op.f("ix_live_activity_starts_client_id"), table_name="live_activity_starts")
    op.drop_table("live_activity_starts")
    op.drop_index(op.f("ix_favorites_entity_type"), table_name="favorites")
    op.drop_index(op.f("ix_favorites_entity_id"), table_name="favorites")
    op.drop_index(op.f("ix_favorites_client_id"), table_name="favorites")
    op.drop_table("favorites")
    op.drop_table("device_tokens")
    op.drop_table("teams")
    op.drop_table("players")
    op.drop_table("match_push_states")
    op.drop_table("live_matches")
    op.drop_table("id_map")
    op.drop_index(op.f("ix_events_circuit"), table_name="events")
    op.drop_table("events")
    op.drop_table("clients")
