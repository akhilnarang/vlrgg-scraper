"""Store the platform of each device token."""

import sqlalchemy as sa

from alembic import op

revision = "9d1e7a3b5c20"
down_revision = "4c6237c07b11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the device token platform; existing tokens are APNs, so they become iOS.

    :return: None.
    """
    op.add_column("device_tokens", sa.Column("platform", sa.String(16), nullable=False, server_default="iOS"))


def downgrade() -> None:
    """Remove the device token platform.

    :return: None.
    """
    with op.batch_alter_table("device_tokens") as batch:
        batch.drop_column("platform")
