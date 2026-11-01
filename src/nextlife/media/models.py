"""Three-tier media pipeline models: ORIGINAL, MASTER, VARIANT (TZ v2.0 Section IV.2)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextlife.storage.models.core import Base


class MediaOriginal(Base):
    """Tier 1: Immutable original uploaded by owner or camera."""
    __tablename__ = "media_original"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # EXIF data extracted and stored in DB, strictly stripped from image binary
    exif_data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    masters: Mapped[list["MediaMaster"]] = relationship(
        "MediaMaster", back_populates="original", cascade="all, delete-orphan"
    )


class MediaMaster(Base):
    """Tier 2: Single normalized master derived strictly from MediaOriginal."""
    __tablename__ = "media_master"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    original_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_original.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    orientation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    crop_box: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    defect_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    original: Mapped["MediaOriginal"] = relationship("MediaOriginal", back_populates="masters")
    variants: Mapped[list["MediaVariant"]] = relationship(
        "MediaVariant", back_populates="master", cascade="all, delete-orphan"
    )


class MediaVariant(Base):
    """Tier 3: Platform-specific derivatives generated strictly from MediaMaster."""
    __tablename__ = "media_variant"
    __table_args__ = (
        CheckConstraint("master_id IS NOT NULL AND length(master_id) > 0", name="chk_variant_must_have_master"),
        UniqueConstraint("master_id", "marketplace_key", "role", "profile_hash", name="uq_variant_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    master_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_master.id", ondelete="CASCADE"), nullable=False, index=True
    )
    marketplace_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), default="GALLERY", nullable=False)  # COVER | GALLERY | DEFECT
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    file_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    master: Mapped["MediaMaster"] = relationship("MediaMaster", back_populates="variants")
