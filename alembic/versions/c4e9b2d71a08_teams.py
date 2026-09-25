"""Store scraped teams and the Redis name-to-ID map."""

import sqlalchemy as sa

from alembic import op

revision = "c4e9b2d71a08"
down_revision = "b7e4c2a91f3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the teams and id_map tables.

    :return: None.
    """
    op.create_table(
        "teams",
        sa.Column("id", sa.String(10), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
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
    )
    op.create_table(
        "id_map",
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("id", sa.String(10), nullable=False),
    )


def downgrade() -> None:
    """Drop the teams and id_map tables.

    :return: None.
    """
    op.drop_table("id_map")
    op.drop_table("teams")
