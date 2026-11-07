#!/usr/bin/env python3
"""Recheck listings whose evidence says PENDING_MODERATION.

The 30-minute timer is not installed: this process has no systemd access.
Run this script again by hand. It does not invent a live listing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nextlife.storage.db import get_db_session  # noqa: E402
from nextlife.storage.models import Listing  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main() -> None:
    pending = []
    with get_db_session() as session:
        rows = session.scalars(select(Listing).where(Listing.item_code == "130")).all()
        for row in rows:
            evidence = row.publish_evidence or {}
            if str(evidence.get("live_status") or "") == "PENDING_MODERATION":
                pending.append({
                    "id": row.id,
                    "marketplace": row.marketplace_key,
                    "url": row.external_url or "",
                })
    print(json.dumps({
        "pending_moderation": pending,
        "timer": "not_installed",
        "note": "Повторный запуск: python scripts/recheck_pending.py",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
