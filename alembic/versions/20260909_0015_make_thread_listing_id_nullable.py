"""make_thread_listing_id_nullable

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-09 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("thread", "listing_id", nullable=True)


def downgrade() -> None:
    op.alter_column("thread", "listing_id", nullable=False)
