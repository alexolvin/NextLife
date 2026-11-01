"""Text and search normalization utilities (TZ §2.5, §9)."""

from __future__ import annotations

import unicodedata
from typing import Any


def normalize_search_text(text: str | None) -> str:
    """Normalize text for uniform case- and diacritic-insensitive search.

    Converts to lower case, strips leading '#', applies Unicode NFKD decomposition
    and removes combining diacritical marks.
    """
    if not text:
        return ""
    cleaned = str(text).strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    decomposed = unicodedata.normalize("NFKD", cleaned.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def build_item_search_text(
    code: str | None,
    title: str | None,
    storage_location: str | None = None,
    description: str | None = None,
    specs: str | None = None,
    notes: str | None = None,
    attrs: dict[str, Any] | None = None,
) -> str:
    """Build normalized search_text for an item record."""
    parts: list[str] = []
    if code:
        parts.append(str(code))
        parts.append(f"#{code}")
    if title:
        parts.append(str(title))
    if storage_location:
        parts.append(str(storage_location))
    if specs:
        parts.append(str(specs))
    if description:
        parts.append(str(description))
    if notes:
        parts.append(str(notes))
    if attrs and isinstance(attrs, dict):
        for k in ("brand", "model", "category"):
            v = attrs.get(k)
            if v and isinstance(v, str):
                parts.append(v)
    raw = " ".join(parts)
    return normalize_search_text(raw)
