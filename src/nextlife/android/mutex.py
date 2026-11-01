"""Global DB mutex of the `android` queue (TZ M1 B.3 property 1).

The queue is processed by one executor strictly sequentially: a single
``bridge_lock`` row (id='android') is claimed atomically
(``UPDATE ... WHERE locked_by IS NULL OR locked_at < now() - ttl``), so a
crashed holder cannot deadlock the queue (stale lock is reclaimable after
the TTL). Mutation D.3-5: removing the mutex from the scenario runner
must make the concurrent-calls test FAIL.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Iterator

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from nextlife.android.errors import BridgeMutexTimeoutError
from nextlife.storage.models import BridgeLock

LOCK_ID = "android"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BridgeMutex:
    """Process- and thread-safe DB mutex for the android queue."""

    def __init__(self, ttl_seconds: float = 60.0, wait_seconds: float = 30.0, poll_seconds: float = 0.1) -> None:
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds

    def _ensure_row(self, session: Session) -> None:
        stmt = insert(BridgeLock).values(id=LOCK_ID).on_conflict_do_nothing()
        session.execute(stmt)

    def _try_claim(self, session: Session, owner_token: str) -> bool:
        self._ensure_row(session)
        stale_before = _utcnow() - timedelta(seconds=self.ttl_seconds)
        stmt = (
            update(BridgeLock)
            .where(
                BridgeLock.id == LOCK_ID,
                or_(BridgeLock.locked_by.is_(None), BridgeLock.locked_at < stale_before),
            )
            .values(locked_by=owner_token, locked_at=_utcnow())
        )
        result = session.execute(stmt)
        session.flush()
        return getattr(result, "rowcount", 0) == 1

    def _release(self, session: Session, owner_token: str) -> None:
        stmt = (
            update(BridgeLock)
            .where(BridgeLock.id == LOCK_ID, BridgeLock.locked_by == owner_token)
            .values(locked_by=None, locked_at=None)
        )
        session.execute(stmt)
        session.flush()

    def is_locked(self, session: Session) -> bool:
        row = session.execute(select(BridgeLock).where(BridgeLock.id == LOCK_ID)).scalar_one_or_none()
        return row is not None and row.locked_by is not None

    @contextmanager
    def hold(self, session: Session, owner_token: str) -> Iterator[None]:
        """Acquire the android queue lock, yield, release.

        The claim is committed so other executors (separate sessions) see
        it immediately; on timeout -> BridgeMutexTimeoutError.
        """
        deadline = time.monotonic() + self.wait_seconds
        claimed = False
        while not claimed:
            try:
                claimed = self._try_claim(session, owner_token)
            except Exception:
                session.rollback()
                raise
            if claimed:
                session.commit()
            else:
                session.rollback()
                if time.monotonic() >= deadline:
                    raise BridgeMutexTimeoutError(
                        f"Could not acquire the 'android' queue mutex within "
                        f"{self.wait_seconds:.0f}s (another executor holds it)."
                    )
                time.sleep(self.poll_seconds)
        try:
            yield
        finally:
            try:
                self._release(session, owner_token)
                session.commit()
            except Exception:
                session.rollback()
                raise
