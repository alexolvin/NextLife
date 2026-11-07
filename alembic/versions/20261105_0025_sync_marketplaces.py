"""0025: Sync marketplaces from config/marketplaces into marketplace table.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-05 10:50:00.000000
"""

from collections.abc import Sequence
from pathlib import Path
import yaml
import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    mp_dir = Path(__file__).resolve().parent.parent.parent / "config" / "marketplaces"
    if mp_dir.is_dir():
        for ypath in sorted(mp_dir.glob("*.yaml")):
            try:
                ydata = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
                m_key = ydata.get("key") or ypath.stem
                m_title = ydata.get("title") or m_key
                m_country = ydata.get("country") or "CZ"
                m_curr = ydata.get("currency") or "CZK"
                m_mode = ydata.get("access_mode") or "DIRECT"
                bind.execute(
                    sa.text(
                        "INSERT INTO marketplace (key, title, country, currency, access_mode, capabilities, enabled, config_hash, created_at, updated_at) "
                        "VALUES (:k, :t, :c, :curr, :mode, '{}', false, 'migration_0025', NOW(), NOW()) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"k": m_key, "t": m_title, "c": m_country, "curr": m_curr, "mode": m_mode},
                )
            except Exception:
                pass


def downgrade() -> None:
    pass
