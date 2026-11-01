"""add_listing_previous_listing_id

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-08 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listing", sa.Column("previous_listing_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_listing_previous_listing_id",
        "listing",
        "listing",
        ["previous_listing_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_listing_previous_listing_id", "listing", ["previous_listing_id"])


def downgrade() -> None:
    op.drop_index("ix_listing_previous_listing_id", table_name="listing")
    op.drop_constraint("fk_listing_previous_listing_id", "listing", type_="foreignkey")
    op.drop_column("listing", "previous_listing_id")
