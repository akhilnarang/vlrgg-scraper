"""add_team_rankings_and_match_ledger

Revision ID: f7a8805679f3
Revises: e2a7c5f90b13
"""

import sqlalchemy as sa

from alembic import op

revision = "f7a8805679f3"
down_revision = "e2a7c5f90b13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the match ledger and team Elo ranking tables.

    :return: None.
    """
    op.create_table(
        "events",
        sa.Column("id", sa.String(10), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("circuit", sa.String(16), nullable=False, server_default="other"),
        sa.Column("circuit_checked_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_events_circuit", "events", ["circuit"])
    op.create_table(
        "matches",
        sa.Column("id", sa.String(10), primary_key=True),
        sa.Column("event_id", sa.String(10), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("played_on", sa.Date(), nullable=False),
        sa.Column("team_a_id", sa.String(10), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("team_b_id", sa.String(10), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("team_a_score", sa.Integer(), nullable=True),
        sa.Column("team_b_score", sa.Integer(), nullable=True),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("ingested_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_matches_event_id", "matches", ["event_id"])
    op.create_index("ix_matches_played_on", "matches", ["played_on"])
    op.create_table(
        "maps",
        sa.Column("match_id", sa.String(10), sa.ForeignKey("matches.id"), primary_key=True),
        sa.Column("map_index", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("team_a_score", sa.Integer(), nullable=False),
        sa.Column("team_b_score", sa.Integer(), nullable=False),
    )
    op.create_table(
        "ranking_results",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.String(10), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("map_index", sa.Integer(), nullable=False),
        sa.Column("played_on", sa.Date(), nullable=False),
        sa.Column("team_a_id", sa.String(10), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("team_b_id", sa.String(10), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("winner_team_id", sa.String(10), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("team_a_elo_before", sa.Float(), nullable=False),
        sa.Column("team_b_elo_before", sa.Float(), nullable=False),
        sa.Column("team_a_delta", sa.Float(), nullable=False),
        sa.UniqueConstraint("match_id", "scope", "map_index"),
    )
    op.create_index("ix_ranking_results_team_a_scope_seq", "ranking_results", ["team_a_id", "scope", "seq"])
    op.create_index("ix_ranking_results_team_b_scope_seq", "ranking_results", ["team_b_id", "scope", "seq"])
    op.create_table(
        "team_elo",
        sa.Column("team_id", sa.String(10), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("match_elo", sa.Float(), nullable=False),
        sa.Column("map_elo", sa.Float(), nullable=False),
        sa.Column("matches", sa.Integer(), nullable=False),
        sa.Column("match_wins", sa.Integer(), nullable=False),
        sa.Column("maps", sa.Integer(), nullable=False),
        sa.Column("map_wins", sa.Integer(), nullable=False),
        sa.Column("first_played_on", sa.Date(), nullable=True),
        sa.Column("last_played_on", sa.Date(), nullable=True),
    )
    op.create_table(
        "team_circuits",
        sa.Column("team_id", sa.String(10), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column("circuit", sa.String(16), primary_key=True),
        sa.Column("matches", sa.Integer(), nullable=False),
        sa.Column("last_played_on", sa.Date(), nullable=False),
    )


def downgrade() -> None:
    """Remove the match ledger and team Elo ranking tables.

    :return: None.
    """
    op.drop_table("team_circuits")
    op.drop_table("team_elo")
    op.drop_index("ix_ranking_results_team_b_scope_seq", table_name="ranking_results")
    op.drop_index("ix_ranking_results_team_a_scope_seq", table_name="ranking_results")
    op.drop_table("ranking_results")
    op.drop_table("maps")
    op.drop_index("ix_matches_played_on", table_name="matches")
    op.drop_index("ix_matches_event_id", table_name="matches")
    op.drop_table("matches")
    op.drop_index("ix_events_circuit", table_name="events")
    op.drop_table("events")
