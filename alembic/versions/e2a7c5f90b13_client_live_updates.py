"""Store whether each client has live updates turned on."""

import sqlalchemy as sa

from alembic import op

revision = "e2a7c5f90b13"
down_revision = "c4e9b2d71a08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the client's live-updates setting; existing clients have not reported it.

    :return: None.
    """
    op.add_column("clients", sa.Column("live_updates", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Remove the client's live-updates setting.

    :return: None.
    """
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("live_updates")
