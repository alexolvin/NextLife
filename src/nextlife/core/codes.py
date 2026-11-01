"""Item code generation and pool management."""

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models import Item


class NoAvailableCodesError(NextLifeError):
    """Raised when all codes 000-999 are exhausted."""


ALL_CODES: Sequence[str] = tuple(f"{i:03d}" for i in range(1000))


def format_code_display(code: str) -> str:
    """Format code for display to owner: e.g. '#348'."""
    return f"#{code}"


def format_code_tag(code: str) -> str:
    """Format code for message headers and email subjects: e.g. '[NL-348]'."""
    return f"[NL-{code}]"


def get_allocated_codes(session: Session, release_days: int) -> set[str]:
    """Return set of codes that are currently active or in cooldown after archival."""
    cutoff_time = datetime.now(UTC) - timedelta(days=release_days)

    stmt = select(Item.code, Item.status, Item.updated_at)
    rows = session.execute(stmt).all()

    allocated: set[str] = set()
    # ТЗ «Склад» §1.2: код DELETED освобождается так же, как ARCHIVED —
    # через code_release_days. Остальные статусы код занимают постоянно.
    for code, status, updated_at in rows:
        if status not in ("ARCHIVED", "DELETED"):
            allocated.add(code)
        else:
            # If archived/deleted, check if cooldown period has expired
            if updated_at is not None and updated_at > cutoff_time:
                allocated.add(code)
    return allocated


def allocate_code(session: Session, release_days: int) -> str:
    """Randomly pick an available 3-digit code from the pool."""
    allocated = get_allocated_codes(session, release_days)
    available = [c for c in ALL_CODES if c not in allocated]

    if not available:
        raise NoAvailableCodesError(
            "Свободных кодов не осталось: все 1000 кодов (000-999) заняты активными или недавно архивированными предметами."
        )

    return random.choice(available)
