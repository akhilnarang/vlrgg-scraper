"""Store write-once match insights."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "2b0955e6c1ed"
down_revision = "b8983d2cb1de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``match_insights``.

    :return: None.
    """
    op.create_table(
        "match_insights",
        sa.Column("match_id", sa.String(length=10), nullable=False),
        sa.Column("prediction", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("prediction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pre_summary", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("pre_summary_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("post_summary", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("post_summary_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("match_id"),
    )


def downgrade() -> None:
    """Drop ``match_insights``.

    :return: None.
    """
    op.drop_table("match_insights")
