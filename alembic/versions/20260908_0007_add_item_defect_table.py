"""add_item_defect_table

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08 08:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_defect",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("defect_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("description_owner", sa.Text(), nullable=False),
        sa.Column("description_cs", sa.Text(), nullable=False),
        sa.Column("photo_id", sa.String(length=36), nullable=False),
        sa.Column("crop_box", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("acknowledged_by_buyer", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_code"], ["item.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("length(photo_id) > 0", name="chk_defect_must_have_photo"),
    )
    op.create_index("ix_item_defect_item_code", "item_defect", ["item_code"])


def downgrade() -> None:
    op.drop_index("ix_item_defect_item_code", table_name="item_defect")
    op.drop_table("item_defect")
