"""drop_lot_pricing

ТЗ «Склад» §6: таблица lot_pricing неиспользуема (модель LotPricing удалена).
downgrade() воссоздаёт таблицу по DDL миграции 0004.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_lot_pricing_item_code", table_name="lot_pricing")
    op.drop_table("lot_pricing")


def downgrade() -> None:
    # DDL миграции 0004 (add_lot_pricing).
    op.create_table(
        "lot_pricing",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("price_gross", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("price_base", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("price_floor", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("negotiable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("shipping_policy", sa.String(length=20), server_default="BUYER_PAYS", nullable=False),
        sa.Column("shipping_included_amount", sa.Numeric(precision=10, scale=2), server_default=sa.text("0.0"), nullable=False),
        sa.Column("price_decay_steps", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[-0.05, -0.10, -0.15]'::jsonb"), nullable=False),
        sa.Column("stale_days", sa.Integer(), server_default="14", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_code"], ["item.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_code", name="uq_lot_pricing_item_code"),
    )
    op.create_index("ix_lot_pricing_item_code", "lot_pricing", ["item_code"])
