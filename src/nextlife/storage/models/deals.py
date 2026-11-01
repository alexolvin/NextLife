"""Deal, Party, Handover, and Settlement models (TZ v2.0 Section II.1 & III).

DoD:
- Models for deal, party, handover, settlement with minimal PII collection.
- Module length <= 400 lines.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextlife.storage.models.core import Base, TimestampMixin

if TYPE_CHECKING:
    from nextlife.storage.models.core import Listing
    from nextlife.storage.models.lots import Lot


class Party(Base, TimestampMixin):
    """Counterparty representation with minimal necessary PII."""

    __tablename__ = "party"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    shipping_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(50), nullable=True)
    anonymized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pii_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deals: Mapped[list["Deal"]] = relationship(back_populates="party")


class Deal(Base, TimestampMixin):
    """Commercial transaction bound to a specific lot and counterparty."""

    __tablename__ = "deal"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lot_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("lot.id", ondelete="SET NULL"), nullable=True, index=True)
    party_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("party.id", ondelete="SET NULL"), nullable=True, index=True)
    listing_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("listing.id"), nullable=True)
    agreed_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    delivery_method: Mapped[str] = mapped_column(String(50), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    pii_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    party: Mapped[Party | None] = relationship(back_populates="deals")
    lot: Mapped["Lot | None"] = relationship()
    listing: Mapped["Listing | None"] = relationship()
    handover: Mapped["Handover | None"] = relationship(back_populates="deal", uselist=False, cascade="all, delete-orphan")
    settlement: Mapped["Settlement | None"] = relationship(back_populates="deal", uselist=False, cascade="all, delete-orphan")


class Handover(Base, TimestampMixin):
    """Physical handover or carrier delivery execution."""

    __tablename__ = "handover"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id: Mapped[str] = mapped_column(String(36), ForeignKey("deal.id", ondelete="CASCADE"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(50), nullable=False)  # PERSONAL / ZASILKOVNA / BALIKOVNA / PPL / POSTA
    place: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    protocol_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    deal: Mapped[Deal] = relationship(back_populates="handover")


class Settlement(Base, TimestampMixin):
    """Financial settlement and breakdown of proceeds per deal."""

    __tablename__ = "settlement"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id: Mapped[str] = mapped_column(String(36), ForeignKey("deal.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    delivery_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    packaging_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    net_payout: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CZK", nullable=False)
    payout_status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deal: Mapped[Deal] = relationship(back_populates="settlement")


class PaymentOption(Base, TimestampMixin):
    """Payment method availability and rules configured for a lot (TZ v2.0 Section III.3.1)."""

    __tablename__ = "payment_option"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lot_id: Mapped[str] = mapped_column(String(36), ForeignKey("lot.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)  # CASH_ON_PICKUP, BANK_TRANSFER_PREPAY, COD, MARKETPLACE_INTERNAL
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(30), nullable=False)  # OWNER / MARKETPLACE
    threshold_applied: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    restriction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    lot: Mapped["Lot"] = relationship()
