"""add_broker_and_service_keys

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-09 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create broker table
    op.create_table(
        "broker",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("sender_on_documents", sa.String(length=64), server_default=sa.text("'BROKER'"), nullable=False),
        sa.Column("cod_payout_destination", sa.String(length=64), server_default=sa.text("'BROKER_ACCOUNT'"), nullable=False),
        sa.Column("claims_handler", sa.String(length=64), server_default=sa.text("'BROKER'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_broker_key"),
    )
    op.create_index("ix_broker_key", "broker", ["key"])

    # 2. Add columns to shipping_service
    op.add_column("shipping_service", sa.Column("broker_key", sa.String(length=64), nullable=True))
    op.add_column("shipping_service", sa.Column("sender_on_documents", sa.String(length=64), server_default=sa.text("'SELLER'"), nullable=False))
    op.add_column("shipping_service", sa.Column("cod_payout_destination", sa.String(length=64), server_default=sa.text("'SELLER_BANK_ACCOUNT'"), nullable=False))
    op.add_column("shipping_service", sa.Column("claims_handler", sa.String(length=64), server_default=sa.text("'CARRIER'"), nullable=False))
    op.add_column("shipping_service", sa.Column("insurance_limit", sa.Numeric(precision=10, scale=2), server_default=sa.text("0.0"), nullable=False))
    op.add_column("shipping_service", sa.Column("price_modifiers", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.create_foreign_key("fk_shipping_service_broker_key", "shipping_service", "broker", ["broker_key"], ["key"], ondelete="SET NULL")
    op.create_index("ix_shipping_service_broker_key", "shipping_service", ["broker_key"])

    # 3. Add column to delivery_option
    op.add_column("delivery_option", sa.Column("broker_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    # 1. Drop delivery_option column
    op.drop_column("delivery_option", "broker_key")

    # 2. Drop shipping_service columns
    op.drop_constraint("fk_shipping_service_broker_key", "shipping_service", type_="foreignkey")
    op.drop_index("ix_shipping_service_broker_key", table_name="shipping_service")
    op.drop_column("shipping_service", "price_modifiers")
    op.drop_column("shipping_service", "insurance_limit")
    op.drop_column("shipping_service", "claims_handler")
    op.drop_column("shipping_service", "cod_payout_destination")
    op.drop_column("shipping_service", "sender_on_documents")
    op.drop_column("shipping_service", "broker_key")

    # 3. Drop broker table
    op.drop_index("ix_broker_key", table_name="broker")
    op.drop_table("broker")
