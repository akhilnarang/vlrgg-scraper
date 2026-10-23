"""Archive Riot games before they are linked to a VLR map."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "8a1c4e7d2b90"
down_revision = "c41e7d2a9f03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make the VLR link optional and enforce one Riot game per linked VLR map.

    :return: None.
    """
    op.drop_constraint("riot_matches_vlr_match_id_map_number_key", "riot_matches", type_="unique")
    op.alter_column("riot_matches", "vlr_match_id", existing_type=sa.String(length=10), nullable=True)
    op.alter_column("riot_matches", "map_number", existing_type=sa.Integer(), nullable=True)
    op.alter_column("riot_matches", "linked_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column(
        "riot_matches",
        "link",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    )
    op.create_index(
        "uq_riot_matches_vlr_match_id_map_number",
        "riot_matches",
        ["vlr_match_id", "map_number"],
        unique=True,
        postgresql_where=sa.text("vlr_match_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Require a VLR link again, aborting rather than deleting games that were never linked.

    The old schema cannot represent an unlinked game, and once Riot's retention window closes its payload is
    unrecoverable, so the downgrade refuses while such rows exist. Export or delete them deliberately first.

    :return: None.
    :raises RuntimeError: When any row is unlinked or only partially linked.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT count(*) FROM riot_matches "
            "WHERE vlr_match_id IS NULL OR map_number IS NULL OR linked_at IS NULL OR link IS NULL"
        )
    ).scalar()
    if rows:
        raise RuntimeError(
            f"{rows} riot_matches rows are unlinked or partial and cannot exist in the old schema. "
            "Export or delete them deliberately before downgrading."
        )
    op.drop_index("uq_riot_matches_vlr_match_id_map_number", table_name="riot_matches")
    op.alter_column("riot_matches", "vlr_match_id", existing_type=sa.String(length=10), nullable=False)
    op.alter_column("riot_matches", "map_number", existing_type=sa.Integer(), nullable=False)
    op.alter_column("riot_matches", "linked_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column(
        "riot_matches",
        "link",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.create_unique_constraint(
        "riot_matches_vlr_match_id_map_number_key", "riot_matches", ["vlr_match_id", "map_number"]
    )
