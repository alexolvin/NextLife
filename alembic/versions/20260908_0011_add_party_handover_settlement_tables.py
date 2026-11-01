"""add_party_handover_settlement_tables

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Party table
    op.create_table(
        "party",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("shipping_address", sa.Text(), nullable=True),
        sa.Column("bank_account", sa.String(length=50), nullable=True),
        sa.Column("anonymized", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("pii_cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_party_email", "party", ["email"])

    # 2. Add deal pii_cleared_at and party FK
    op.add_column("deal", sa.Column("pii_cleared_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_deal_party_id",
        "deal",
        "party",
        ["party_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Handover table
    op.create_table(
        "handover",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deal_id", sa.String(length=36), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=False),
        sa.Column("place", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tracking_number", sa.String(length=100), nullable=True),
        sa.Column("tracking_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="PENDING", nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["deal_id"], ["deal.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_handover_deal_id", "handover", ["deal_id"])

    # 4. Settlement table
    op.create_table(
        "settlement",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deal_id", sa.String(length=36), nullable=False),
        sa.Column("gross_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("platform_fee", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("delivery_cost", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("packaging_cost", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("net_payout", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CZK", nullable=False),
        sa.Column("payout_status", sa.String(length=30), server_default="PENDING", nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["deal_id"], ["deal.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deal_id", name="uq_settlement_deal_id"),
    )


def downgrade() -> None:
    op.drop_table("settlement")
    op.drop_index("ix_handover_deal_id", table_name="handover")
    op.drop_table("handover")
    op.drop_constraint("fk_deal_party_id", "deal", type_="foreignkey")
    op.drop_column("deal", "pii_cleared_at")
    op.drop_index("ix_party_email", table_name="party")
    op.drop_table("party")
