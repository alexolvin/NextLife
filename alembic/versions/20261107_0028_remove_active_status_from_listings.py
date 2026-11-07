"""0028: Remove ACTIVE status from listings and migrate to PUBLISHED.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-07 03:00:00.000000
"""

from collections.abc import Sequence
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE listing SET status = 'PUBLISHED' WHERE status = 'ACTIVE'")


def downgrade() -> None:
    pass
