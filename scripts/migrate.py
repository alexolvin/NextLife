#!/usr/bin/env python3
"""Run Alembic database migrations cleanly (Task K1-014)."""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config

root_dir = Path(__file__).resolve().parent.parent
os.chdir(root_dir)
if str(root_dir / "src") not in sys.path:
    sys.path.insert(0, str(root_dir / "src"))

dsn = os.environ.get("NEXTLIFE_DSN")
if not dsn:
    raise RuntimeError("Missing required environment variable: NEXTLIFE_DSN")

parsed = urlsplit(dsn)
db_name_url = parsed.path.lstrip("/")

# 1. Check database name from URL before connecting
if db_name_url not in ("nextlife", "nextlife_test"):
    raise RuntimeError(
        f"Safety refusal: refused to run migrations against unexpected database '{db_name_url}'. "
        "Allowed databases: 'nextlife', 'nextlife_test'."
    )

# 2. Check actual database name from PostgreSQL server before any DDL
try:
    from nextlife.storage.db import get_engine
    from sqlalchemy import text
    # Single DSN source: the same NEXTLIFE_DSN the app resolves via get_engine().
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT current_database();")).fetchone()
    current_db = row[0] if row else ""
except Exception as exc:
    raise RuntimeError(f"Failed to connect to database for pre-migration check: {exc}") from exc

if current_db not in ("nextlife", "nextlife_test"):
    raise RuntimeError(
        f"Safety refusal: connected server database '{current_db}' does not match expected databases "
        "('nextlife', 'nextlife_test'). Aborting before any DDL."
    )

alembic_cfg = Config(str(root_dir / "alembic.ini"))
command.upgrade(alembic_cfg, "head")
print(f"Alembic upgrade head completed successfully on database '{current_db}' (schema public).")
