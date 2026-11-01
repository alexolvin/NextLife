"""Czech re-commerce phrase dictionary and anti-calque validator (TZ v2.0 Section V.2)."""

import re
from pathlib import Path
from typing import Any

import yaml

from nextlife.core.exceptions import NextLifeError

DEFAULT_PHRASES_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "czech_phrases.yaml"


class CzechCalqueDetectedError(NextLifeError):
    """Raised when Russian or English calques are detected in Czech listing text."""


def load_czech_phrases_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load Czech phrases configuration dictionary."""
    cfg_file = Path(config_path) if config_path else DEFAULT_PHRASES_PATH
    if not cfg_file.exists():
        return {}
    try:
        return yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def get_czech_phrase(category: str, key: str, **kwargs: Any) -> str:
    """Retrieve an idiomatic Czech phrase by category and key."""
    data = load_czech_phrases_config()
    phrases = data.get("phrases", {}).get(category, {})
    template = phrases.get(key, "")
    if kwargs and template:
        return str(template.format(**kwargs))
    return str(template)


def validate_czech_listing_text(
    text: str,
    config_path: Path | str | None = None,
) -> None:
    """Validate Czech listing text against forbidden literal calques from Russian/English.

    DoD:
    - Text containing calques from Russian or English strictly fails validation with CzechCalqueDetectedError.
    """
    if not text:
        return

    data = load_czech_phrases_config(config_path)
    forbidden_list = data.get("forbidden_calques", [])

    text_lower = text.lower()
    for calque in forbidden_list:
        c_lower = calque.strip().lower()
        if not c_lower:
            continue
        # Check boundary or substring
        if re.search(r'\b' + re.escape(c_lower) + r'\b', text_lower, re.IGNORECASE) or c_lower in text_lower:
            raise CzechCalqueDetectedError(
                f"Forbidden calque '{calque}' detected in Czech text. "
                "Listings must use native Czech idioms from config/czech_phrases.yaml."
            )
