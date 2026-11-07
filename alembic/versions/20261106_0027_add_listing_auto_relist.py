"""0027: Add auto_relist column to listing table.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-06 23:45:00.000000
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listing",
        sa.Column("auto_relist", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("listing", "auto_relist")
