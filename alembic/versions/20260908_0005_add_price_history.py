"""add_price_history

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08 07:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("old_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("new_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("initiator", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_code"], ["item.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_history_item_code", "price_history", ["item_code"])


def downgrade() -> None:
    op.drop_index("ix_price_history_item_code", table_name="price_history")
    op.drop_table("price_history")
