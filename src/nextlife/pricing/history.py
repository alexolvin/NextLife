"""Price change history tracking and draft approval invalidation (TZ v2.0 Section II.5, III.1)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from nextlife.core.status import ItemStatus
from nextlife.storage.models.core import Approval, Base, Item


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False, index=True)
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    initiator: Mapped[str] = mapped_column(String(20), nullable=False)  # OWNER | AGENT | SYSTEM
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


def record_price_change(
    session: Session,
    item_code: str,
    new_price: Decimal,
    initiator: str = "OWNER",
    reason: str = "Manual price update",
) -> PriceHistory:
    """Record price modification, update item price, and invalidate draft approval.

    DoD:
    - Records history with initiator and reason.
    - Price modification invalidates any existing approval (resets item status to DRAFT).
    """
    item = session.execute(select(Item).where(Item.code == item_code)).scalar_one_or_none()
    if not item:
        raise ValueError(f"Item #{item_code} not found")

    old_price = item.target_price
    item.target_price = new_price

    # 1. Add price history entry
    history_entry = PriceHistory(
        item_code=item_code,
        old_price=old_price,
        new_price=new_price,
        initiator=initiator,
        reason=reason,
    )
    session.add(history_entry)

    # 2. DoD: Price change strictly invalidates previous approval
    # Delete or invalidate existing approvals for this item
    session.query(Approval).filter(Approval.item_code == item_code).delete()

    # If item was approved, revert to DRAFT so it cannot be published without re-approval
    if item.status == ItemStatus.APPROVED.value:
        item.status = ItemStatus.DRAFT.value

    session.commit()
    return history_entry


def get_item_price_history(session: Session, item_code: str) -> list[dict[str, Any]]:
    """Retrieve full price history for an item, sorted newest to oldest."""
    stmt = (
        select(PriceHistory)
        .where(PriceHistory.item_code == item_code)
        .order_by(PriceHistory.changed_at.desc())
    )
    rows = session.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "item_code": r.item_code,
            "old_price": r.old_price,
            "new_price": r.new_price,
            "initiator": r.initiator,
            "reason": r.reason,
            "changed_at": r.changed_at.isoformat(),
        }
        for r in rows
    ]
