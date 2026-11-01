"""add channel_canary — TZ M1 D.1: daily live probes per enabled channel.

State of each enabled (marketplace, scenario, channel) is stored here:
OK / BLOCKED, last success, block reason and live-probe artifact path.
Channel status on the «Площадки» screen and in reports comes from this
table (Ч-1: status only from a live probe < 24 h old).

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-03 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "channel_canary",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "marketplace_key",
            sa.String(length=50),
            sa.ForeignKey("marketplace.key"),
            nullable=False,
        ),
        sa.Column("scenario", sa.String(length=30), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False, server_default="BLOCKED"),
        sa.Column("probe_query", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_count", sa.Integer(), nullable=True),
        sa.Column("date_absent_on_site", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("artifact_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("marketplace_key", "scenario", "channel", name="uq_canary_channel"),
        sa.CheckConstraint("state IN ('OK', 'BLOCKED')", name="ck_canary_state"),
        sa.CheckConstraint(
            "channel IN ('DIRECT', 'API', 'ANDROID', 'EXCLUDED')", name="ck_canary_channel"
        ),
    )
    op.create_index("ix_channel_canary_marketplace_key", "channel_canary", ["marketplace_key"])


def downgrade() -> None:
    op.drop_index("ix_channel_canary_marketplace_key", table_name="channel_canary")
    op.drop_table("channel_canary")
