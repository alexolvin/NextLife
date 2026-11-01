"""Shipping and logistics ORM models (TZ v2.0 Section III.2, II.2)."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nextlife.storage.models.core import Item

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextlife.storage.models.core import Base


class Carrier(Base):
    __tablename__ = "carrier"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    services: Mapped[list["ShippingService"]] = relationship("ShippingService", back_populates="carrier")


class Broker(Base):
    __tablename__ = "broker"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_on_documents: Mapped[str] = mapped_column(String(64), default="BROKER", nullable=False)
    cod_payout_destination: Mapped[str] = mapped_column(String(64), default="BROKER_ACCOUNT", nullable=False)
    claims_handler: Mapped[str] = mapped_column(String(64), default="BROKER", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    services: Mapped[list["ShippingService"]] = relationship("ShippingService", back_populates="broker")


class ShippingService(Base):
    __tablename__ = "shipping_service"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    carrier_key: Mapped[str] = mapped_column(String(64), ForeignKey("carrier.key"), nullable=False, index=True)
    broker_key: Mapped[str | None] = mapped_column(String(64), ForeignKey("broker.key"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    service_type: Mapped[str] = mapped_column(String(64), nullable=False)
    weight_max_g: Mapped[int] = mapped_column(Integer, nullable=False)
    dim_rule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CZK", nullable=False)
    cod_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cod_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.0"), nullable=False)
    insurance_included: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.0"), nullable=False)
    insurance_limit: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.0"), nullable=False)
    insurance_max: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.0"), nullable=False)
    sender_on_documents: Mapped[str] = mapped_column(String(64), default="SELLER", nullable=False)
    cod_payout_destination: Mapped[str] = mapped_column(String(64), default="SELLER_BANK_ACCOUNT", nullable=False)
    claims_handler: Mapped[str] = mapped_column(String(64), default="CARRIER", nullable=False)
    price_modifiers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    countries: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    delivery_days: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    carrier: Mapped["Carrier"] = relationship("Carrier", back_populates="services")
    broker: Mapped["Broker | None"] = relationship("Broker", back_populates="services")


class DeliveryOption(Base):
    __tablename__ = "delivery_option"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False, index=True)
    service_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shipping_service.id"), nullable=True)
    carrier_key: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service_name: Mapped[str] = mapped_column(String(128), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_manual_price: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    item: Mapped["Item"] = relationship("Item", back_populates="delivery_options")
    service: Mapped["ShippingService | None"] = relationship("ShippingService")
