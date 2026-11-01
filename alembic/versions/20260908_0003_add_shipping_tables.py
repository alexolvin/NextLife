"""add_shipping_tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-08 07:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create carrier table
    op.create_table(
        "carrier",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_carrier_key"),
    )
    op.create_index("ix_carrier_key", "carrier", ["key"])

    # 2. Create shipping_service table
    op.create_table(
        "shipping_service",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("carrier_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("service_type", sa.String(length=64), nullable=False),
        sa.Column("weight_max_g", sa.Integer(), nullable=False),
        sa.Column("dim_rule", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CZK", nullable=False),
        sa.Column("cod_supported", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cod_fee", sa.Numeric(precision=10, scale=2), server_default=sa.text("0.0"), nullable=False),
        sa.Column("insurance_included", sa.Numeric(precision=10, scale=2), server_default=sa.text("0.0"), nullable=False),
        sa.Column("insurance_max", sa.Numeric(precision=10, scale=2), server_default=sa.text("0.0"), nullable=False),
        sa.Column("countries", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[\"CZ\"]'::jsonb"), nullable=False),
        sa.Column("delivery_days", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["carrier_key"], ["carrier.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipping_service_carrier_key", "shipping_service", ["carrier_key"])

    # 3. Create delivery_option table
    op.create_table(
        "delivery_option",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=True),
        sa.Column("carrier_key", sa.String(length=64), nullable=False),
        sa.Column("service_name", sa.String(length=128), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_manual_price", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_code"], ["item.code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["shipping_service.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_option_item_code", "delivery_option", ["item_code"])

    # 4. Add logistics and specifications columns to item table
    op.add_column("item", sa.Column("weight_g", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("dim_l_mm", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("dim_w_mm", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("dim_h_mm", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("packed_weight_g", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("packed_l_mm", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("packed_w_mm", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("packed_h_mm", sa.Integer(), nullable=True))
    op.add_column("item", sa.Column("fragile", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("item", sa.Column("hazardous", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("item", sa.Column("serial_number", sa.Text(), nullable=True))
    op.add_column("item", sa.Column("serial_photo_id", sa.String(length=36), nullable=True))
    op.add_column("item", sa.Column("box_present", sa.Boolean(), nullable=True))
    op.add_column("item", sa.Column("receipt_present", sa.Boolean(), nullable=True))
    op.add_column("item", sa.Column("purchase_date", sa.Date(), nullable=True))
    op.add_column("item", sa.Column("manufacturer_warranty_until", sa.Date(), nullable=True))
    op.add_column("item", sa.Column("condition_grade", sa.String(length=10), nullable=True))
    op.add_column("item", sa.Column("usage_profile", sa.Text(), nullable=True))
    op.add_column("item", sa.Column("completeness", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    # 1. Drop item columns
    op.drop_column("item", "completeness")
    op.drop_column("item", "usage_profile")
    op.drop_column("item", "condition_grade")
    op.drop_column("item", "manufacturer_warranty_until")
    op.drop_column("item", "purchase_date")
    op.drop_column("item", "receipt_present")
    op.drop_column("item", "box_present")
    op.drop_column("item", "serial_photo_id")
    op.drop_column("item", "serial_number")
    op.drop_column("item", "hazardous")
    op.drop_column("item", "fragile")
    op.drop_column("item", "packed_h_mm")
    op.drop_column("item", "packed_w_mm")
    op.drop_column("item", "packed_l_mm")
    op.drop_column("item", "packed_weight_g")
    op.drop_column("item", "dim_h_mm")
    op.drop_column("item", "dim_w_mm")
    op.drop_column("item", "dim_l_mm")
    op.drop_column("item", "weight_g")

    # 2. Drop delivery_option table
    op.drop_index("ix_delivery_option_item_code", table_name="delivery_option")
    op.drop_table("delivery_option")

    # 3. Drop shipping_service table
    op.drop_index("ix_shipping_service_carrier_key", table_name="shipping_service")
    op.drop_table("shipping_service")

    # 4. Drop carrier table
    op.drop_index("ix_carrier_key", table_name="carrier")
    op.drop_table("carrier")
