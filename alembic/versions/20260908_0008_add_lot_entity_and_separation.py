"""add_lot_entity_and_separation

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-08 08:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lot",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="DRAFT", nullable=False),
        sa.Column("price_gross", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("price_floor", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="CZK", nullable=False),
        sa.Column("shipping_policy", sa.String(length=20), server_default="BUYER_PAYS", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["item.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_lot_code"),
    )
    op.create_index("ix_lot_code", "lot", ["code"])
    op.create_index("ix_lot_item_id", "lot", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_lot_item_id", table_name="lot")
    op.drop_index("ix_lot_code", table_name="lot")
    op.drop_table("lot")
