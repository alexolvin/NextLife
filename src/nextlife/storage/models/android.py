"""ANDROID bridge state (TZ M1 B.3 property 1, B.2/B.3 property 4)."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nextlife.storage.models.core import Base


class BridgeLock(Base):
    """Global DB mutex of the `android` queue (TZ M1 B.3, property 1).

    Single row (id='android'): the queue is processed by one executor
    strictly sequentially. ``locked_by``/``locked_at`` are claimed
    atomically; a stale lock (crashed holder) is reclaimable after the TTL.
    """

    __tablename__ = "bridge_lock"
    __table_args__ = (CheckConstraint("id = 'android'", name="ck_bridge_lock_single_row"),)

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BridgeDailyUsage(Base):
    """Per-marketplace daily action counter (TZ M1 B.2/B.3, property 4).

    Daily caps live in the config (``android_bridge.daily_caps``, set by
    the Owner per confirmation 8); a marketplace without a configured cap
    gets no ANDROID actions.
    """

    __tablename__ = "bridge_daily_usage"
    __table_args__ = (
        UniqueConstraint("marketplace_key", "day", name="uq_bridge_usage_day"),
        CheckConstraint("actions >= 0", name="ck_bridge_usage_nonnegative"),
    )

    marketplace_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    actions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
