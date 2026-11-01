"""add_outbox_table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07 18:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create outbox table
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("transport", sa.String(length=20), nullable=False),
        sa.Column("recipient_hint", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delivery_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),
        sa.CheckConstraint(
            "(state != 'SENT') OR (delivery_evidence IS NOT NULL AND delivery_evidence != '{}'::jsonb)",
            name="outbox_sent_evidence_not_empty",
        ),
    )

    # 2. Add columns to message table if they don't exist
    op.add_column(
        "message",
        sa.Column(
            "transport",
            sa.String(length=20),
            server_default="MARKETPLACE",
            nullable=False,
        ),
    )
    op.add_column(
        "message",
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint("uq_message_external_id", "message", ["external_message_id"])


def downgrade() -> None:
    op.drop_constraint("uq_message_external_id", "message", type_="unique")
    op.drop_column("message", "external_message_id")
    op.drop_column("message", "transport")
    op.drop_table("outbox")
