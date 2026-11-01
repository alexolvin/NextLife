"""Legal disclaimer generation and validation (TZ v2.0 Section III.4.1).

DoD:
- Legal text must come verbatim from config/legal/<country>.yaml.
- Generation of legal text by LLM/model is strictly forbidden.
- Missing legal file or key raises MissingLegalConfigError and excludes block.
- Validator ensures mandatory Czech clauses are present (§ 1916 OZ, soukromý prodej, 14-day return exclusion).
"""

from pathlib import Path
from typing import Any

import yaml

from nextlife.templates.renderer import render_template

CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "config" / "legal"


class LegalError(Exception):
    """Base exception for legal disclaimer module."""
    pass


class MissingLegalConfigError(LegalError):
    """Raised when legal configuration file or key is missing."""
    pass


class InvalidLegalDisclaimerError(LegalError):
    """Raised when listing text lacks mandatory legal disclaimers."""
    pass


def load_legal_config(country: str = "cz") -> dict[str, Any]:
    """Load legal disclaimer config verbatim from config/legal/<country>.yaml."""
    cfg_file = CONFIG_ROOT / f"{country.lower()}.yaml"
    if not cfg_file.exists():
        raise MissingLegalConfigError(f"Legal config file missing: {cfg_file}")

    with open(cfg_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    disclaimer = data.get("legal_disclaimer")
    if not disclaimer or not disclaimer.get("paragraphs"):
        raise MissingLegalConfigError(f"Missing 'legal_disclaimer.paragraphs' in {cfg_file}")

    return data


def render_legal_disclaimer(country: str = "cz") -> str:
    """Render verbatim legal disclaimer from owner configuration without LLM involvement."""
    cfg = load_legal_config(country)
    disclaimer = cfg["legal_disclaimer"]
    paragraphs = disclaimer.get("paragraphs", [])

    return render_template(
        template_name=f"{country.lower()}_disclaimer.jinja2",
        context={"paragraphs": paragraphs, "title": disclaimer.get("title", "")},
        subpath="legal",
    )


def validate_legal_disclaimer(listing_text: str, country: str = "cz") -> bool:
    """Validate that listing text contains mandatory legal disclaimer clauses verbatim.

    Mandatory clauses for CZ (§ 1916 OZ, soukromý prodej, 14 dnů):
    - 'soukromý prodej'
    - '1916'
    - '14 dnů'
    """
    if country.lower() != "cz":
        return True

    mandatory_phrases = [
        "soukromý prodej",
        "1916",
        "14 dnů",
    ]

    for phrase in mandatory_phrases:
        if phrase.lower() not in listing_text.lower():
            raise InvalidLegalDisclaimerError(
                f"Listing text lacks mandatory legal clause: '{phrase}'"
            )

    return True
