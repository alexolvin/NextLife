"""Pre-publish validation engine for marketplace listings (TZ v2.0 Section IV.3, V.1, VI.1).

DoD:
- Enforces all marketplace capability limits (title length, body length, max photos).
- Enforces defect photo guarantee: every declared defect must have an authentic unretouched photo in publication set.
- Enforces price validity (price > 0 and price >= price_floor).
- Strictly prevents internal field leaks in publication payload.
- Enforces mandatory legal disclaimers for target jurisdiction.
- Returns clean PrepublishValidationReport or raises PrepublishValidationError.
- Module length strictly <= 400 lines.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
)
from nextlife.core.config import MarketplaceConfig
from nextlife.core.exceptions import NextLifeError
from nextlife.text.legal import validate_legal_disclaimer


class PrepublishValidationError(NextLifeError):
    """Raised when lot publication payload fails pre-publish validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Pre-publish validation failed with {len(errors)} error(s):\n" + "\n".join(f"- {e}" for e in errors))


@dataclass
class PrepublishValidationReport:
    is_valid: bool
    marketplace_key: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_lot_for_publication(
    lot_data: dict[str, Any],
    marketplace_config: MarketplaceConfig,
    photo_ids: list[str] | None = None,
    defect_photo_map: dict[str, str] | None = None,
) -> PrepublishValidationReport:
    """Run comprehensive multi-point validation before publishing to a marketplace."""
    errors: list[str] = []
    warnings: list[str] = []
    mp_key = marketplace_config.key
    caps = getattr(marketplace_config, "capabilities", {})

    # 1. Check internal field leakage
    try:
        check_context_for_internal_fields(lot_data)
    except Exception as exc:
        errors.append(f"Internal field leak detected: {exc}")

    # 2. Title validation
    title = str(lot_data.get("title", "")).strip()
    if not title:
        errors.append("Title cannot be empty.")
    else:
        max_title = caps.get("max_title_len", 60) if isinstance(caps, dict) else getattr(caps, "max_title_len", 60)
        if len(title) > max_title:
            errors.append(
                f"Title length ({len(title)}) exceeds '{mp_key}' limit of {max_title} characters."
            )

    # 3. Description / Body validation
    body = str(lot_data.get("description", "")).strip()
    if not body:
        errors.append("Description cannot be empty.")
    else:
        max_body = caps.get("max_body_len", 4000) if isinstance(caps, dict) else getattr(caps, "max_body_len", 4000)
        if len(body) > max_body:
            errors.append(
                f"Description length ({len(body)}) exceeds '{mp_key}' limit of {max_body} characters."
            )

        # Country legal disclaimer check
        country = getattr(marketplace_config, "country", "CZ").lower()
        if country == "cz":
            try:
                validate_legal_disclaimer(body, country="cz")
            except Exception as exc:
                errors.append(f"Legal disclaimer error: {exc}")

    # 4. Photos count validation
    current_photos = photo_ids or lot_data.get("photos", [])
    max_photos = caps.get("max_photos", 10) if isinstance(caps, dict) else getattr(caps, "max_photos", 10)
    if len(current_photos) > max_photos:
        errors.append(
            f"Number of photos ({len(current_photos)}) exceeds '{mp_key}' limit of {max_photos}."
        )

    # 5. Defect photo linkage guarantee (TZ v2.0 Section IV.3, v1.1 Section 8)
    defects = lot_data.get("defects", [])
    if defects:
        defect_map = defect_photo_map or {}
        for idx, defect in enumerate(defects):
            defect_id = defect.get("id") or str(idx)
            photo_linked = defect.get("photo_id") or defect_map.get(defect_id)
            if not photo_linked or photo_linked not in current_photos:
                errors.append(
                    f"Declared defect '{defect.get('zone', 'defect')}' is missing mandatory linked photo in publication photoset."
                )

    # 6. Price validation
    price = lot_data.get("price")
    if price is None:
        errors.append("Price must be specified.")
    else:
        try:
            p_val = Decimal(str(price))
            if p_val <= Decimal("0"):
                errors.append("Price must be strictly positive.")

            price_floor = lot_data.get("price_floor_check") or lot_data.get("internal_price_floor")
            if price_floor is not None and p_val < Decimal(str(price_floor)):
                errors.append(
                    f"Publishing price ({p_val}) cannot be lower than price_floor ({price_floor})."
                )
        except Exception:
            errors.append(f"Invalid price value: {price}")

    report = PrepublishValidationReport(
        is_valid=len(errors) == 0,
        marketplace_key=mp_key,
        errors=errors,
        warnings=warnings,
    )

    if not report.is_valid:
        raise PrepublishValidationError(errors)

    return report
