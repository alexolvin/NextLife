"""Lot entity representing the commercial offering for a physical item (TZ v2.0 Section II.1).

DoD:
- Commercial fields (code, prices, status, shipping_policy) separated from physical item.
- Relisting the same item creates a new Lot record with a distinct 3-character code and links to item_id.
- Module length <= 400 lines.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextlife.storage.models.core import Base, TimestampMixin

if TYPE_CHECKING:
    from nextlife.storage.models.core import Item


class Lot(Base, TimestampMixin):
    """Commercial offering for an item on marketplaces."""

    __tablename__ = "lot"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    code: Mapped[str] = mapped_column(String(3), unique=True, index=True, nullable=False)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("item.code", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default="DRAFT", nullable=False)
    price_gross: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_floor: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="CZK", nullable=False)
    # TZ-NextLife-UI-N2 §1.2: внутренняя отчётная сверка (главная валюта).
    # Не публикуется: в outgoing-данные не попадает (см. core.internal_fields).
    price_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    base_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shipping_policy: Mapped[str] = mapped_column(String(20), default="BUYER_PAYS", nullable=False)
    reserved_for_party_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    item: Mapped["Item"] = relationship("Item", backref="lots")
