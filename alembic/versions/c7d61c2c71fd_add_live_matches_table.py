"""add_live_matches_table

Revision ID: c7d61c2c71fd
Revises: d94cce987756
"""

import sqlalchemy as sa

from alembic import op

revision = "c7d61c2c71fd"
down_revision = "d94cce987756"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the live-match details and tracker-score store.

    :return: None.
    """
    op.create_table(
        "live_matches",
        sa.Column("match_id", sa.String(10), primary_key=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("detail_fetched_at", sa.Integer(), nullable=True),
        sa.Column("video", sa.Text(), nullable=True),
        sa.Column("video_received_at", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    """Drop the live-match store.

    :return: None.
    """
    op.drop_table("live_matches")
