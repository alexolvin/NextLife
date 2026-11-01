"""Item defect entity and mandatory photo linkage (TZ v2.0 Section II.3, IV.3)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models.core import Base


class DefectValidationError(NextLifeError):
    """Raised when a defect is declared without a mandatory linked photo or coordinates."""


class ItemDefect(Base):
    __tablename__ = "item_defect"
    __table_args__ = (
        CheckConstraint("length(photo_id) > 0", name="chk_defect_must_have_photo"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False, index=True)
    defect_type: Mapped[str] = mapped_column(String(32), nullable=False)  # scratch | dent | crack | dead_pixel | wear | other
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # cosmetic | functional | critical
    description_owner: Mapped[str] = mapped_column(Text, nullable=False)
    description_cs: Mapped[str] = mapped_column(Text, nullable=False)

    # Mandatory linked photo
    photo_id: Mapped[str] = mapped_column(String(36), nullable=False)

    # Specific coordinates on photo (x, y, w, h) for arrow / highlight visualization
    crop_box: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    acknowledged_by_buyer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class DefectCoordinateBox(BaseModel):
    x: int = Field(..., ge=0, description="Top-left X coordinate on photo in px")
    y: int = Field(..., ge=0, description="Top-left Y coordinate on photo in px")
    width: int = Field(..., gt=0, description="Bounding width in px")
    height: int = Field(..., gt=0, description="Bounding height in px")


class DefectSchema(BaseModel):
    defect_type: str
    severity: str
    description_owner: str
    description_cs: str
    photo_id: str = Field(..., min_length=1, description="Mandatory photo ID showing the defect")
    crop_box: DefectCoordinateBox

    @field_validator("photo_id")
    @classmethod
    def validate_photo_mandatory(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("DoD: Declared defect must have a mandatory linked photo (photo_id)!")
        return v.strip()


def validate_defect_data(defect_dict: dict[str, Any]) -> DefectSchema:
    """Validate defect payload ensuring photo_id and highlight coordinates are strictly present."""
    photo_id = defect_dict.get("photo_id")
    if not photo_id or str(photo_id).strip() == "":
        raise DefectValidationError(
            f"Defect '{defect_dict.get('description_owner', 'unnamed')}' lacks mandatory linked photo (photo_id). "
            "A defect cannot be published without an explicit macro photograph."
        )

    try:
        return DefectSchema(**defect_dict)
    except Exception as exc:
        raise DefectValidationError(f"Invalid defect specification: {exc}") from exc
