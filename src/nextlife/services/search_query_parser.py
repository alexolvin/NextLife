"""Search query parser supporting complex tags (+ and -).

Rule K-1: Module must strictly be <= 400 lines.
Supports:
  +word or +"exact phrase": mandatory presence in title or description.
  -word or -"exact phrase": mandatory absence in title or description.
Implementation: post-filtering of listings after marketplace retrieval.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Token pattern:
# Captures optional prefix '+' or '-' followed by either a quoted phrase or a non-space token.
_TOKEN_PATTERN = re.compile(
    r'(?P<prefix>[+-])?(?:\"(?P<phrase>[^\"]+)\"|(?P<word>[^\s\"]+))'
)


def _strip_diacritics(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class ParsedQuery:
    raw_query: str
    marketplace_query: str
    required_terms: tuple[str, ...] = field(default_factory=tuple)
    excluded_terms: tuple[str, ...] = field(default_factory=tuple)
    base_terms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_tags(self) -> bool:
        return bool(self.required_terms or self.excluded_terms)


def parse_search_query(query: str) -> ParsedQuery:
    """Parse search string into marketplace query and +/- filter terms."""
    raw = (query or "").strip()
    if not raw:
        return ParsedQuery(raw_query="", marketplace_query="")

    required: list[str] = []
    excluded: list[str] = []
    base_terms: list[str] = []
    mkt_terms: list[str] = []

    for m in _TOKEN_PATTERN.finditer(raw):
        prefix = m.group("prefix")
        phrase = m.group("phrase")
        word = m.group("word")

        term = (phrase or word or "").strip()
        if not term:
            continue

        if prefix == "+":
            required.append(term)
            mkt_terms.append(f'"{term}"' if " " in term else term)
        elif prefix == "-":
            excluded.append(term)
        else:
            base_terms.append(term)
            mkt_terms.append(f'"{term}"' if " " in term else term)

    mkt_q = " ".join(mkt_terms).strip()
    # Fallback to base terms if all were excluded or empty
    if not mkt_q and base_terms:
        mkt_q = " ".join(base_terms).strip()

    return ParsedQuery(
        raw_query=raw,
        marketplace_query=mkt_q,
        required_terms=tuple(required),
        excluded_terms=tuple(excluded),
        base_terms=tuple(base_terms),
    )


def term_in_text(term: str, text: str) -> bool:
    """Check if term exists in text (case/diacritic-insensitive, supporting phrases and collapsed whitespace/hyphens)."""
    t_clean = term.strip().lower()
    if not t_clean:
        return True
    text_clean = text.lower()

    t_diacr = _strip_diacritics(t_clean)
    text_diacr = _strip_diacritics(text_clean)

    # 1. Multi-word phrase matching with whitespace normalization
    if " " in t_clean:
        norm_term = " ".join(t_clean.split())
        norm_text = " ".join(text_clean.split())
        if norm_term in norm_text:
            return True
        norm_term_d = " ".join(t_diacr.split())
        norm_text_d = " ".join(text_diacr.split())
        if norm_term_d in norm_text_d:
            return True
    else:
        # Word boundary / non-word boundaries match
        for t, txt in ((t_clean, text_clean), (t_diacr, text_diacr)):
            pattern = rf"(?<!\w){re.escape(t)}(?!\w)"
            if re.search(pattern, txt):
                return True
            if t in txt:
                return True

    # 2. Collapsed alphanumeric match (handles 32gb vs 32 gb, rtx4070 vs rtx 4070)
    collapsed_term = re.sub(r"[\s\-_]", "", t_diacr)
    if len(collapsed_term) >= 3:
        collapsed_text = re.sub(r"[\s\-_]", "", text_diacr)
        if collapsed_term in collapsed_text:
            return True

    return False


def matches_query_tags(
    title: str,
    description: str | None,
    parsed: ParsedQuery,
) -> bool:
    """Post-check if title and description satisfy all required and excluded terms."""
    if not parsed.has_tags:
        return True

    full_text = f"{title or ''} {description or ''}"

    # All required terms (+) MUST be present
    for req in parsed.required_terms:
        if not term_in_text(req, full_text):
            return False

    # None of the excluded terms (-) MAY be present
    for exc in parsed.excluded_terms:
        if term_in_text(exc, full_text):
            return False

    return True


def filter_by_query_tags(
    items: list[T],
    parsed: ParsedQuery,
    text_extractor: Callable[[T], tuple[str, str | None]],
) -> list[T]:
    """Filter arbitrary items based on title and description extracted by callback."""
    if not parsed.has_tags:
        return list(items)

    filtered: list[T] = []
    for it in items:
        title, desc = text_extractor(it)
        if matches_query_tags(title, desc, parsed):
            filtered.append(it)
    return filtered


_DESC_SELECTORS = (
    ".c-item-detail__description",
    ".p-inzerat-detail__description",
    "[data-testid='description']",
    ".popis",
    ".c-description",
    ".description",
    ".inzeratydetail",
)


def fetch_listing_description(url: str, timeout: float = 2.5) -> str:
    """Best-effort fetch of listing description from external page for tag checking."""
    if not url or not url.startswith("http"):
        return ""
    try:
        import httpx
        from bs4 import BeautifulSoup

        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=timeout,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for sel in _DESC_SELECTORS:
            el = soup.select_one(sel)
            if el:
                return el.get_text(" ", strip=True)
        text = soup.get_text(" ", strip=True)
        if "Popis nabídky" in text:
            return text.split("Popis nabídky", 1)[1][:5000].strip()
        if "Popis:" in text:
            return text.split("Popis:", 1)[1][:5000].strip()
        return text[:5000]
    except Exception:
        return ""
