"""Wizard step 1: search across marketplaces and normalize results.

The lot-creation wizard's first step lets the owner enter a search key, run a
search on the available marketplaces (Vinted required; Bazos/Aukro when their
connector is present), and select relevant listings. This module provides the
pure normalization + Aukro «Kup teď» (buy-now) filtering + the intent-based
relevance heuristic. Live search is performed by the caller (connectors /
phone broker) via the ``search_fn`` hook; this module does no I/O and is
tested offline with fixtures.
"""

import re
import unicodedata
from collections.abc import Callable
from typing import Any

from nextlife.connectors.base import RawListing
from nextlife.core.config import ConfigBundle, load_config

# A marketplace whose search results are buy-now only (Aukro «Kup teď»);
# auctions and non-buy-now lots are filtered out.
_BUY_NOW_PLATFORMS = ("aukro_cz",)

# Titles containing any of these tokens describe a complete PC (or a kit
# around a GPU), not the GPU itself. Compared diacritic-insensitively
# (NFD-normalized) and case-insensitively, on word boundaries only.
_PC_TITLE_TOKENS = (
    "pc",
    "sestava",
    "sestavu",
    "sestave",
    "herni",
    "gaming",
    "komplet",
    "kompletni",
    "build",
    "skrin",
    "компьютер",
    "computer",
    "workstation",
)


def _normalize_title(title: str) -> str:
    """Lowercase + strip diacritics so «Herní» matches the token «herni»."""
    decomposed = unicodedata.normalize("NFD", title)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


def _title_has_token(title: str, token: str) -> bool:
    """Word-boundary match of ``token`` in ``title`` (diacritic/case-insensitive)."""
    return re.search(rf"\b{re.escape(token.lower())}\b", _normalize_title(title)) is not None


# Titles containing any of these tokens are a BUYING request (the poster wants
# to acquire the item), not a sale — irrelevant to a seller looking for
# reference listings. Compared diacritic/case-insensitively, word boundaries.
_BUYING_REQUEST_TOKENS = (
    "koupim",  # koupím (CS) — "I will buy"
    "kupim",   # kúpim (SK) / kupim (CS) — "I will buy"
    "vymenim", # vyměním (CS) — "I will exchange"
    "kuplyu",  # куплю (RU) — "I will buy"
)


def _is_buying_request(title: str) -> bool:
    """True when the title is a buying/exchange request, not a sale."""
    return any(_title_has_token(title, t) for t in _BUYING_REQUEST_TOKENS)


def listing_matches_query(title: str, query: str, extra_text: str = "") -> bool:
    """True when the query string occurs in the listing title or text.

    Diacritic/case-insensitive word-boundary match (same NFD normalization as
    the intent filter). An empty query matches everything. Listings that lack
    the searched string are irrelevant (owner, 2026-10-19: «ищу 3090,
    показывают 3080») and are dropped BEFORE the photo fetch.

    Word-boundary (not substring) so «tricko» does not match «elektrickou»
    (owner, 2026-10-20: Bazos showed a gas stove for a t-shirt search).
    """
    q = _normalize_title(query)
    if not q:
        return True
    return re.search(rf"\b{re.escape(q)}\b", _normalize_title(f"{title} {extra_text}".strip())) is not None


def _listing_extra_text(raw: RawListing) -> str:
    """Card text beyond the title: condition_text + the connector's
    ``raw["desc"]`` (Vinted Android cards carry the card content-desc there)."""
    parts = [raw.condition_text or ""]
    if isinstance(raw.raw, dict):
        parts.append(str(raw.raw.get("desc", "")))
    return " ".join(p for p in parts if p)


def filter_by_query(raw_listings: list[RawListing], query: str) -> list[RawListing]:
    """Drop listings whose title/text lack the query string or violate +/- tags (pure).

    Buying requests (the poster wants to acquire the item, «Koupím …») are
    also dropped — they are not sales, so they are not useful reference
    listings for a seller (owner 2026-10-20: Bazos results «не релевантные»).
    """
    from nextlife.services.search_query_parser import matches_query_tags, parse_search_query

    parsed = parse_search_query(query)
    terms_to_check = parsed.base_terms if parsed.has_tags else ([query] if query.strip() else [])
    out: list[RawListing] = []
    for r in raw_listings:
        extra = _listing_extra_text(r)
        if any(not listing_matches_query(r.title, t, extra) for t in terms_to_check):
            continue
        if parsed.has_tags and not matches_query_tags(r.title, extra, parsed):
            continue
        if _is_buying_request(r.title):
            continue
        out.append(r)
    return out


def filter_results_by_intent(results: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
    """Filter raw wizard search results by the owner's stated intent.

    - ``"gpu"`` — keep rows whose title has NO PC-indicating token (the card
      itself, not a complete PC built around it).
    - ``"pc"`` — keep rows whose title HAS a PC-indicating token (a complete PC).
    - anything else (``"any"``, empty, unknown) — no filtering.

    Pure function; the client-side mirror in ``lot_wizard_search.html`` must
    stay in sync with ``_PC_TITLE_TOKENS``.
    """
    if intent == "gpu":
        return [r for r in results if not any(_title_has_token(str(r.get("title", "")), t) for t in _PC_TITLE_TOKENS)]
    if intent == "pc":
        return [r for r in results if any(_title_has_token(str(r.get("title", "")), t) for t in _PC_TITLE_TOKENS)]
    return list(results)


def normalize_listing(raw: RawListing, platform: str) -> dict[str, Any] | None:
    """Normalize a RawListing into a wizard result entry.

    Returns None when the listing must be skipped: for buy-now platforms
    (Aukro) only «Kup teď» lots are selectable — ``buy_now_active`` true and
    not an auction. Other platforms pass through as ``type: "listing"``.
    """
    entry: dict[str, Any] = {
        "title": raw.title,
        "price": str(raw.price),
        "currency": raw.currency,
        "url": raw.external_url,
        "platform": platform,
        "type": "listing",
        "image": raw.raw.get("image_url", "") if isinstance(raw.raw, dict) else "",
    }
    if platform in _BUY_NOW_PLATFORMS:
        if raw.raw.get("auction") or not raw.raw.get("buy_now_active"):
            return None
        entry["type"] = "buy_now"
    return entry


def search_across_marketplaces(
    query: str,
    marketplaces: list[str] | None = None,
    search_fn: Callable[[str, str], list[RawListing]] | None = None,
    config: ConfigBundle | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search across marketplaces, normalize results, filter Aukro to Kup teď.

    ``search_fn(query, mp_key) -> list[RawListing]`` performs the live search.
    When None, the registry web connectors are used (Bazos/Aukro). Vinted
    search (ANDROID, phone broker) is not a web connector — the caller injects
    it via ``search_fn``. Per-marketplace failures are reported in ``errors``
    (honest NOT-STARTED/BLOCKED), never fabricated.
    """
    cfg = config or load_config()
    mps = marketplaces or list(cfg.marketplaces.keys())
    results: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    for mp_key in mps:
        if mp_key not in cfg.marketplaces:
            errors[mp_key] = "not_configured"
            continue
        try:
            if search_fn is not None:
                raw_listings = search_fn(query, mp_key)
            else:
                from nextlife.connectors.registry import get_connector

                connector = get_connector(mp_key, cfg)
                raw_listings = connector.search(query)
        except Exception as exc:
            errors[mp_key] = f"{type(exc).__name__}: {exc}"
            continue
        for raw in raw_listings[:limit]:
            entry = normalize_listing(raw, mp_key)
            if entry is not None:
                results.append(entry)

    return {"query": query, "results": results, "errors": errors}
