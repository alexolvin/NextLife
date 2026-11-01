"""add_buy_request_polling_and_candidate_dedup

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-08 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update buy_request
    op.add_column(
        "buy_request",
        sa.Column("poll_interval_seconds", sa.Integer(), server_default="60", nullable=False),
    )
    op.add_column(
        "buy_request",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Update buy_candidate
    op.add_column(
        "buy_candidate",
        sa.Column("external_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "buy_candidate",
        sa.Column("notified_owner", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_unique_constraint(
        "uq_buy_candidate_request_url",
        "buy_candidate",
        ["request_id", "external_url"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_buy_candidate_request_url", "buy_candidate", type_="unique")
    op.drop_column("buy_candidate", "notified_owner")
    op.drop_column("buy_candidate", "external_id")
    op.drop_column("buy_request", "last_polled_at")
    op.drop_column("buy_request", "poll_interval_seconds")
