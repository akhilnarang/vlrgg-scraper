"""Store Riot esports games linked to VLR maps."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c41e7d2a9f03"
down_revision = "2b0955e6c1ed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``riot_matches``.

    :return: None.
    """
    op.create_table(
        "riot_matches",
        sa.Column("riot_id", sa.Text(), nullable=False),
        sa.Column("vlr_match_id", sa.String(length=10), nullable=False),
        sa.Column("map_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("link", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("riot_id"),
        sa.UniqueConstraint("vlr_match_id", "map_number"),
    )


def downgrade() -> None:
    """Drop ``riot_matches``.

    :return: None.
    """
    op.drop_table("riot_matches")
