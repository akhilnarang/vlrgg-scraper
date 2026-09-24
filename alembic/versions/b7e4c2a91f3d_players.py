"""Store scraped players, starting with their current team."""

import sqlalchemy as sa

from alembic import op

revision = "b7e4c2a91f3d"
down_revision = "9d1e7a3b5c20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the players table.

    :return: None.
    """
    op.create_table(
        "players",
        sa.Column("id", sa.String(10), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("team_id", sa.String(10), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.Integer(), nullable=False),
        sa.Column("last_fetched_at", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    """Drop the players table.

    :return: None.
    """
    op.drop_table("players")
