"""add_media_pipeline_tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08 07:58:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. media_original
    op.create_table(
        "media_original",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=3), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_bytes", sa.Integer(), nullable=False),
        sa.Column("exif_data", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_code"], ["item.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_original_item_code", "media_original", ["item_code"])
    op.create_index("ix_media_original_sha256", "media_original", ["sha256"])

    # 2. media_master
    op.create_table(
        "media_master",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("original_id", sa.String(length=36), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("orientation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("crop_box", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_cover", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("defect_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["original_id"], ["media_original.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_master_original_id", "media_master", ["original_id"])

    # 3. media_variant (DoD: Cannot be created without master_id)
    op.create_table(
        "media_variant",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("master_id", sa.String(length=36), nullable=False),
        sa.Column("marketplace_key", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="GALLERY", nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("file_bytes", sa.Integer(), nullable=False),
        sa.Column("stale", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["master_id"], ["media_master.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("master_id IS NOT NULL AND length(master_id) > 0", name="chk_variant_must_have_master"),
        sa.UniqueConstraint("master_id", "marketplace_key", "role", "profile_hash", name="uq_variant_idempotency_key"),
    )
    op.create_index("ix_media_variant_master_id", "media_variant", ["master_id"])
    op.create_index("ix_media_variant_marketplace_key", "media_variant", ["marketplace_key"])


def downgrade() -> None:
    op.drop_index("ix_media_variant_marketplace_key", table_name="media_variant")
    op.drop_index("ix_media_variant_master_id", table_name="media_variant")
    op.drop_table("media_variant")

    op.drop_index("ix_media_master_original_id", table_name="media_master")
    op.drop_table("media_master")

    op.drop_index("ix_media_original_sha256", table_name="media_original")
    op.drop_index("ix_media_original_item_code", table_name="media_original")
    op.drop_table("media_original")
