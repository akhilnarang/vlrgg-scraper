"""add_region_to_team_elo

Revision ID: d94cce987756
Revises: f7a8805679f3
"""

import sqlalchemy as sa

from alembic import op

revision = "d94cce987756"
down_revision = "f7a8805679f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the competitive region a team's recent tiered events classify to.

    :return: None.
    """
    op.add_column("team_elo", sa.Column("region", sa.String(16), nullable=True))
    op.execute("DELETE FROM ranking_results")


def downgrade() -> None:
    """Remove the stored competitive region.

    :return: None.
    """
    op.drop_column("team_elo", "region")
