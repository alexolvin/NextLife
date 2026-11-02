"""Structured listing text assembly and validation (TZ v2.0 Section I.2, III.4, V.1, V.2).

DoD:
- Assembles complete structured listing text with all mandatory sections:
  1. Title / Headline
  2. Condition block (grade, definition, box, receipt, warranty, guarantee)
  3. Usage profile (GPU mining negation, battery health)
  4. Defects with photo references
  5. Completeness / accessories
  6. Delivery & pickup options with explicit buyer totals
  7. Legal disclaimer verbatim from config
- Passes all strict validators: facts, Czech calques, legal, outgoing internal field filter.
- Enforces marketplace length limits from capabilities.
- Module length strictly <= 400 lines.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nextlife.core.exceptions import NextLifeError
from nextlife.templates.renderer import render_template
from nextlife.text.czech import validate_czech_listing_text
from nextlife.text.facts import validate_listing_facts
from nextlife.text.legal import render_legal_disclaimer, validate_legal_disclaimer
from nextlife.text.usage import format_usage_profile_cs

CONDITIONS_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "conditions.yaml"
)
MARKETPLACES_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "marketplaces"
)


class AssemblerError(NextLifeError):
    """Base error for text assembler."""
    pass


class MarketplaceLimitExceededError(AssemblerError):
    """Raised when listing text or title exceeds marketplace capabilities."""
    pass


@dataclass
class AssembledListing:
    title: str
    body: str
    char_count: int
    marketplace_key: str | None = None


def load_conditions_config() -> dict[str, Any]:
    """Load condition definitions from config/conditions.yaml."""
    if not CONDITIONS_CONFIG_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(CONDITIONS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return dict(data.get("grades", {}))
    except Exception:
        return {}


def load_marketplace_limits(marketplace_key: str) -> dict[str, int]:
    """Load text limits from config/marketplaces/<key>.yaml."""
    cfg_file = MARKETPLACES_DIR / f"{marketplace_key}.yaml"
    if not cfg_file.exists():
        return {}
    try:
        data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        caps = data.get("capabilities", {})
        return {
            "max_title_len": caps.get("max_title_len", 60),
            "max_body_len": caps.get("max_body_len", 4000),
        }
    except Exception:
        return {}


def assemble_listing_text(
    item_data: dict[str, Any],
    delivery_options: list[dict[str, Any]],
    marketplace_key: str | None = None,
    country: str = "cz",
) -> AssembledListing:
    """Assemble complete listing text adhering to strict re-commerce standards."""
    title = str(item_data.get("title", "")).strip()
    if not title:
        raise AssemblerError("Listing title cannot be empty.")

    # 1. Condition grade
    grades = load_conditions_config()
    grade_code = item_data.get("condition_grade", "B").upper()
    grade_info = grades.get(grade_code, {
        "label_cs": "Dobrý stav",
        "definition_cs": "Běžné známky používání, plně funkční.",
    })

    # 2. Box & receipt
    box_present = bool(item_data.get("box_present", False))
    receipt_present = bool(item_data.get("receipt_present", False))

    # 3. Warranty & seller guarantee
    warranty_text = ""
    if receipt_present and item_data.get("manufacturer_warranty_until"):
        warranty_text = f"do {item_data['manufacturer_warranty_until']} (doklad přiložen)"

    seller_guarantee_days = int(item_data.get("seller_guarantee_days", 0))
    seller_guarantee_text = ""
    if seller_guarantee_days > 0:
        seller_guarantee_text = f"{seller_guarantee_days} dní na důkladné vyzkoušení s možností vrácení peněz"

    # 4. Usage profile
    usage_profile = format_usage_profile_cs(
        category_key=str(item_data.get("category", "")),
        owner_usage_declaration=item_data.get("usage_declaration"),
        battery_health_pct=item_data.get("battery_health_pct"),
        is_gpu="gpu" in str(item_data.get("category", "")).lower() or item_data.get("is_gpu", False),
        mining=bool(item_data.get("mining", False)),
    )

    # 5. Defects
    defects = item_data.get("defects") or []

    # 6. Completeness
    completeness = item_data.get("completeness") or []

    # 7. Delivery options formatting (filtered for marketplace compatibility)
    if marketplace_key:
        from nextlife.services.marketplace_shipping import filter_compatible_delivery_options
        delivery_options, _ = filter_compatible_delivery_options(delivery_options, marketplace_key)

    formatted_delivery = []
    for opt in delivery_options:
        name = opt.get("name", "Doprava")
        buyer_total = opt.get("buyer_total")
        unit = opt.get("currency", "Kč")
        details = opt.get("details", "")
        total_str = f"{buyer_total} {unit}" if buyer_total is not None else "dle domluvy"
        formatted_delivery.append({
            "name": name,
            "buyer_total_str": total_str,
            "details": details,
        })

    # 8. Legal disclaimer verbatim
    legal_disclaimer = render_legal_disclaimer(country)

    # 9. Context preparation
    context = {
        "title": title,
        "condition_grade": grade_code,
        "condition_grade_label": grade_info.get("label_cs", ""),
        "condition_grade_definition": grade_info.get("definition_cs", ""),
        "usage_profile": usage_profile,
        "description": item_data.get("description", ""),
        "box_present": box_present,
        "receipt_present": receipt_present,
        "warranty_text": warranty_text,
        "seller_guarantee_text": seller_guarantee_text,
        "defects": defects,
        "completeness": completeness,
        "delivery_options": formatted_delivery,
        "legal_disclaimer": legal_disclaimer,
    }

    # 10. Render via centralized Jinja2 renderer (enforces outgoing field filter)
    body = render_template("structured_cz.jinja2", context, subpath="listing")

    # 11. Strict validation pipeline
    validate_listing_facts(body, item_data)
    validate_czech_listing_text(body)
    validate_legal_disclaimer(body, country)

    # 12. Enforce marketplace limits
    if marketplace_key:
        limits = load_marketplace_limits(marketplace_key)
        max_title = limits.get("max_title_len", 60)
        max_body = limits.get("max_body_len", 4000)

        if len(title) > max_title:
            raise MarketplaceLimitExceededError(
                f"Title length {len(title)} exceeds {marketplace_key} limit of {max_title} characters."
            )
        if len(body) > max_body:
            raise MarketplaceLimitExceededError(
                f"Body length {len(body)} exceeds {marketplace_key} limit of {max_body} characters."
            )

    return AssembledListing(
        title=title,
        body=body,
        char_count=len(body),
        marketplace_key=marketplace_key,
    )
