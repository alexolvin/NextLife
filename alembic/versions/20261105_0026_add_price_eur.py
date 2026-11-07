"""0026: Add price_eur column to item and set 1050 for item 130 per TZ-v2 §4.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-05 12:17:00.000000
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    # Add price_eur column if it does not already exist
    op.add_column("item", sa.Column("price_eur", sa.Numeric(12, 2), nullable=True))
    # Set 1050 EUR for item 130 per TZ-v2 section 4
    conn.execute(sa.text("UPDATE item SET price_eur = 1050 WHERE code = '130'"))


def downgrade() -> None:
    op.drop_column("item", "price_eur")
