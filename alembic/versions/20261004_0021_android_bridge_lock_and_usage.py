"""add bridge_lock + bridge_daily_usage — TZ M1 B.3/B.2.

bridge_lock: global DB mutex for the `android` queue (B.3 property 1):
the queue is processed by a single executor strictly sequentially.
A single row (id='android') is atomically claimed; a stale lock
(holder crash) is reclaimable after the TTL.

bridge_daily_usage: per-marketplace daily action counter (B.2/B.3
property 4): daily caps live in config (Owner confirmation 8); a
marketplace without a configured cap gets no ANDROID actions.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-04 02:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "bridge_lock",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 'android'", name="ck_bridge_lock_single_row"),
    )
    op.create_table(
        "bridge_daily_usage",
        sa.Column("marketplace_key", sa.String(length=50), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("actions", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("marketplace_key", "day"),
        sa.CheckConstraint("actions >= 0", name="ck_bridge_usage_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("bridge_daily_usage")
    op.drop_table("bridge_lock")
