"""add_protocol_path_to_handover

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-08 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("handover", sa.Column("protocol_path", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("handover", "protocol_path")
