"""SQLAlchemy ORM models for NextLife according to section 5."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nextlife.storage.models.shipping import DeliveryOption

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Item(Base, TimestampMixin):
    __tablename__ = "item"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # SELL / BUY
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category_key: Mapped[str] = mapped_column(String(50), nullable=False)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    condition_key: Mapped[str] = mapped_column(String(50), nullable=False)
    defects: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    target_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Склад (ТЗ «Склад» вместо «Ленты», этап 1): канонические текстовые поля
    description_lang: Mapped[str | None] = mapped_column(String(10), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[str | None] = mapped_column(Text, nullable=True)
    specs: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_location: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ТЗ Реструктуризация UI: Гарантия, Оценка, Поиск (§2.5)
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warranty_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    price_estimate_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_estimate_median: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_estimate_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_estimate_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_estimate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    search_text: Mapped[str | None] = mapped_column(Text, nullable=True, default="")

    # Physical dimensions & weight
    weight_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dim_l_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dim_w_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dim_h_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Packaging dimensions & weight (strictly separated from item fields)
    packed_weight_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packed_l_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packed_w_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packed_h_mm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Item details and logistics flags
    fragile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hazardous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    serial_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial_photo_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    box_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    receipt_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manufacturer_warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    condition_grade: Mapped[str | None] = mapped_column(String(10), nullable=True)
    usage_profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    completeness: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    photos: Mapped[list["ItemPhoto"]] = relationship(back_populates="item")
    listings: Mapped[list["Listing"]] = relationship(back_populates="item")
    delivery_options: Mapped[list["DeliveryOption"]] = relationship("DeliveryOption", back_populates="item")


class ItemPhoto(Base, TimestampMixin):
    __tablename__ = "item_photo"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # ORIGINAL / DEFECT / PUBLISH
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    derived_from_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("item_photo.id"), nullable=True
    )
    transform_log: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vlm_verdict: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    item: Mapped[Item] = relationship(back_populates="photos")


class Marketplace(Base, TimestampMixin):
    __tablename__ = "marketplace"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    access_mode: Mapped[str] = mapped_column(String(20), nullable=False)  # DIRECT / ANDROID
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ResearchRun(Base, TimestampMixin):
    __tablename__ = "research_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    marketplaces: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    results: Mapped[list["ResearchResult"]] = relationship(back_populates="run")


class ResearchResult(Base, TimestampMixin):
    __tablename__ = "research_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("research_run.id"), nullable=False)
    marketplace_key: Mapped[str] = mapped_column(
        String(50), ForeignKey("marketplace.key"), nullable=False
    )
    external_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fx_rate_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    condition_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    run: Mapped[ResearchRun] = relationship(back_populates="results")


class Listing(Base, TimestampMixin):
    __tablename__ = "listing"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False)
    marketplace_key: Mapped[str] = mapped_column(
        String(50), ForeignKey("marketplace.key"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    previous_listing_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("listing.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ТЗ Реструктуризация UI (§2.5)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lang: Mapped[str | None] = mapped_column(String(10), nullable=True)
    photo_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    category_auto: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    item: Mapped[Item] = relationship(back_populates="listings")


class Thread(Base, TimestampMixin):
    __tablename__ = "thread"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    listing_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("listing.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # MARKETPLACE / EMAIL
    counterparty: Mapped[str] = mapped_column(String(255), nullable=False)
    lang: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    messages: Mapped[list["Message"]] = relationship(back_populates="thread")


class Message(Base, TimestampMixin):
    __tablename__ = "message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("thread.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # IN / OUT
    transport: Mapped[str] = mapped_column(
        String(20), default="MARKETPLACE", nullable=False
    )  # TELEGRAM / EMAIL / MARKETPLACE
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    text_original: Mapped[str] = mapped_column(Text, nullable=False)
    lang_original: Mapped[str] = mapped_column(String(10), nullable=False)
    text_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(20), nullable=True)  # AGENT / OWNER
    template_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    thread: Mapped[Thread] = relationship(back_populates="messages")


class Outbox(Base, TimestampMixin):
    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint(
            "(state != 'SENT') OR (delivery_evidence IS NOT NULL AND delivery_evidence != '{}'::jsonb)",
            name="outbox_sent_evidence_not_empty",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    transport: Mapped[str] = mapped_column(String(20), nullable=False)  # TELEGRAM / EMAIL
    recipient_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), default="PENDING", nullable=False
    )  # PENDING / PULLED / SENT / FAILED
    pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)




class FxRate(Base, TimestampMixin):
    __tablename__ = "fx_rate"

    rate_date: Mapped[datetime] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate_to_base: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class BuyRequest(Base, TimestampMixin):
    __tablename__ = "buy_request"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candidates: Mapped[list["BuyCandidate"]] = relationship(back_populates="request")


class BuyCandidate(Base, TimestampMixin):
    __tablename__ = "buy_candidate"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("buy_request.id"), nullable=False
    )
    marketplace_key: Mapped[str] = mapped_column(
        String(50), ForeignKey("marketplace.key"), nullable=False
    )
    external_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_base: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    contacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notified_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    request: Mapped[BuyRequest] = relationship(back_populates="candidates")


class Approval(Base, TimestampMixin):
    __tablename__ = "approval"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)


class Job(Base, TimestampMixin):
    __tablename__ = "job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class OwnerNotification(Base, TimestampMixin):
    __tablename__ = "owner_notification"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Immutable audit event log. Only INSERT allowed; UPDATE/DELETE blocked by DB trigger."""

    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    actor: Mapped[str] = mapped_column(String(20), nullable=False)  # AGENT / OWNER / SYSTEM
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class TruthLog(Base):
    """Immutable truth assertion log with mandatory evidence."""

    __tablename__ = "truth_log"
    __table_args__ = (
        CheckConstraint(
            "evidence IS NOT NULL AND evidence != '{}'::jsonb",
            name="truth_log_evidence_not_empty",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    recipient: Mapped[str] = mapped_column(String(30), nullable=False)  # OWNER / COUNTERPARTY
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
