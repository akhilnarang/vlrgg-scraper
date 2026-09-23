"""Initial live-update schema."""

import sqlalchemy as sa

from alembic import op

revision = "4c6237c07b11"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the initial live-update tables.

    :return: None.
    """
    op.create_table("clients", sa.Column("id", sa.String(36), primary_key=True))
    op.create_table(
        "device_tokens",
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token", sa.String(4096), nullable=False),
    )
    op.create_table(
        "favorites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.String(10), nullable=False),
        sa.UniqueConstraint("client_id", "entity_type", "entity_id"),
    )
    op.create_index("ix_favorites_client_id", "favorites", ["client_id"])
    op.create_index("ix_favorites_entity_type", "favorites", ["entity_type"])
    op.create_index("ix_favorites_entity_id", "favorites", ["entity_id"])
    op.create_table(
        "match_push_states",
        sa.Column("match_id", sa.String(10), primary_key=True),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("last_state_json", sa.Text(), nullable=True),
    )
    op.create_table(
        "live_activity_starts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("match_id", sa.String(10), nullable=False),
        sa.UniqueConstraint("client_id", "match_id"),
    )
    op.create_index("ix_live_activity_starts_client_id", "live_activity_starts", ["client_id"])
    op.create_index("ix_live_activity_starts_match_id", "live_activity_starts", ["match_id"])


def downgrade() -> None:
    """Remove the live-update tables.

    :return: None.
    """
    op.drop_table("live_activity_starts")
    op.drop_table("match_push_states")
    op.drop_table("favorites")
    op.drop_table("device_tokens")
    op.drop_table("clients")
