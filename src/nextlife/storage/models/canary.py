"""Channel canary state (TZ M1 D.1: daily live probes per enabled channel)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nextlife.storage.models.core import Base, TimestampMixin


class ChannelCanary(Base, TimestampMixin):
    """One row per enabled (marketplace, scenario, channel) — live-probe state.

    Ч-1: channel status comes from a live probe (< 24 h), not from memory.
    D.1: schema drift / empty / protection page / unknown redirect -> BLOCKED
    with reason and artifact path; owner notified via outbox.
    """

    __tablename__ = "channel_canary"
    __table_args__ = (
        UniqueConstraint("marketplace_key", "scenario", "channel", name="uq_canary_channel"),
        CheckConstraint("state IN ('OK', 'BLOCKED')", name="ck_canary_state"),
        CheckConstraint("channel IN ('DIRECT', 'API', 'ANDROID', 'EXCLUDED')", name="ck_canary_channel"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace_key: Mapped[str] = mapped_column(
        String(50), ForeignKey("marketplace.key"), nullable=False, index=True
    )
    scenario: Mapped[str] = mapped_column(String(30), nullable=False)  # search / publish / ...
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="BLOCKED")
    probe_query: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the site's search cards do not expose a date (e.g. Sbazar):
    # the canary then requires posted_at to be None (no fabricated dates).
    date_absent_on_site: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
