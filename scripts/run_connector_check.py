#!/usr/bin/env python3
"""Run daily connector check and update status (TZ v2 §7.2)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nextlife.services.connectors_map import (
    compute_connectors_map,
    format_owner_message,
    run_regular_check,
)
from nextlife.storage.db import get_db_session


def main():
    with get_db_session() as session:
        check_res = run_regular_check(session)
        print("Connector regular check finished:", check_res)
        statuses = compute_connectors_map(session)
        msg = format_owner_message(statuses)
        print("\n--- Current Connectors Status ---")
        print(msg)


if __name__ == "__main__":
    main()
