"""Factual consistency verification between generated listing text and item data (TZ v2.0 Section V.3)."""

import re
from typing import Any

from nextlife.core.exceptions import NextLifeError


class FactualInconsistencyError(NextLifeError):
    """Raised when generated listing text contains unverified claims or hallucinations."""


ACCESSORY_KEYWORDS = {
    "kabel": "kabel",
    "nabíječk": "nabíječka",
    "adaptér": "adaptér",
    "pouzdro": "pouzdro/obal",
    "obal": "obal",
    "krabic": "krabice",
    "sluchátk": "sluchátka",
    "záruční list": "záruční list",
    "doklad": "doklad o koupi",
    "faktur": "faktura",
}


def validate_listing_facts(text: str, item_data: dict[str, Any]) -> None:
    """Validate that every factual claim in text is backed by item attributes.

    DoD:
    - Text mentioning accessories ('kabel v balení / kompletu') when completeness is empty strictly fails.
    - Warranty claims without documented warranty date fail.
    - Claims of 'bez vad' when defects exist fail.
    """
    if not text:
        return

    text_lower = text.lower()
    completeness = [str(x).lower() for x in (item_data.get("completeness") or [])]
    box_present = item_data.get("box_present", False)
    receipt_present = item_data.get("receipt_present", False)
    warranty_until = item_data.get("manufacturer_warranty_until")
    defects = item_data.get("defects") or []

    # 1. Accessories & completeness check
    for kw, acc_name in ACCESSORY_KEYWORDS.items():
        # Check if keyword is mentioned as included / in package
        pattern = rf"(?:v\s+balení|v\s+kompletu|součástí|součást|přidám|k\s+tomu|obsahuje|včetně)\s+[^.\n]*?\b{kw}"
        pattern_rev = rf"\b{kw}\w*\s+(?:v\s+balení|součástí|v\s+kompletu)"

        if re.search(pattern, text_lower) or re.search(pattern_rev, text_lower):
            # If it's krabice and box_present is True, allowed
            if "krabic" in kw and box_present:
                continue
            # If it's doklad/faktura and receipt_present is True, allowed
            if ("doklad" in kw or "faktur" in kw) and receipt_present:
                continue

            # Otherwise, must be present in completeness list
            found_in_completeness = any(kw in c for c in completeness)
            if not found_in_completeness:
                raise FactualInconsistencyError(
                    f"Factual hallucination: text claims '{acc_name}' is included, "
                    f"but completeness is empty or does not list '{acc_name}'!"
                )

    # 2. Warranty claim check
    warranty_patterns = [r"\bv\s+záruce\b", r"\bse\s+zárukou\b", r"\bplatná\s+záruka\b"]
    for wp in warranty_patterns:
        if re.search(wp, text_lower) and not warranty_until:
            raise FactualInconsistencyError(
                "Factual hallucination: text claims item is under warranty, "
                "but manufacturer_warranty_until is null/undocumented."
            )

    # 3. Defect denial check
    if defects:
        if "bez vad" in text_lower or "žádné vady" in text_lower or "bez škrábanců" in text_lower:
            raise FactualInconsistencyError(
                "Factual inconsistency: text claims 'bez vad', "
                f"but item has {len(defects)} declared defect(s) in database!"
            )
