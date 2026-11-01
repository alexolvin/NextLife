"""Parse search results from a ui_dump XML (Stage 2: one parser, config diffs)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass
class SearchResult:
    """One parsed result card from the search results screen."""

    title: str
    price: str
    currency: str
    card_desc: str


# Currency patterns: 3-letter code or symbol (CZK, EUR, PLN, Kč, €, zloty)
_PRICE_RE = re.compile(r"(\d[\d.,]*)\s*([A-Z]{3}|Kč|€|zł|zloty|Kčs)")
# Currency codes/symbols actually used on marketplace cards. A bare 3-letter
# run ([A-Z]{3}) also matches title words ("RTX 3060 TUF" -> price "3060",
# owner report 2026-10-19), so a match with an unknown currency is used only
# when no known-currency match exists.
_KNOWN_CURRENCIES = frozenset(
    {"CZK", "EUR", "PLN", "GBP", "USD", "HUF", "SEK", "DKK", "NOK", "CHF", "Kč", "Kčs", "€", "zł", "zloty"}
)
# Known key prefixes that start a metadata segment (not the title)
_META_KEYS = re.compile(r",\s*(Značka|Marke|Značka|Brand|Kondycja|Condition|Stav|Condition|Model|Model)\s*:", re.I)


def parse_results(xml: str, card_selector: str, limit: int = 5) -> list[SearchResult]:
    """Parse result cards from the search results screen XML.

    ``card_selector`` is either:
    - a resource-id string (e.g. ``item_box_container``) — matches nodes by resource-id
    - a ``content-desc:PATTERN`` string — matches nodes whose content-desc contains PATTERN

    Handles concatenated XML (multiple ``<hierarchy>`` roots from scrolling).
    The parser extracts title, price and currency from the card's ``content-desc``.
    """
    if not xml or not card_selector:
        return []
    results: list[SearchResult] = []
    seen: set[str] = set()
    use_desc = card_selector.startswith("content-desc:")
    desc_pattern = card_selector[len("content-desc:"):] if use_desc else ""

    # Split concatenated XML into individual documents
    docs = re.findall(r"<hierarchy[^>]*>.*?</hierarchy>", xml, re.DOTALL)
    if not docs:
        docs = [xml]

    for doc in docs:
        try:
            root = ElementTree.fromstring(doc)
        except ElementTree.ParseError:
            continue
        for node in root.iter("node"):
            if use_desc:
                desc = node.get("content-desc", "")
                if desc_pattern not in desc:
                    continue
            else:
                if node.get("resource-id", "") != card_selector:
                    continue
                desc = node.get("content-desc", "")
            if not desc or desc in seen:
                continue
            seen.add(desc)
            title, price, currency = _parse_desc(desc)
            results.append(SearchResult(title=title, price=price, currency=currency, card_desc=desc))
            if len(results) >= limit:
                return results
    return results


def _parse_desc(desc: str) -> tuple[str, str, str]:
    """Extract (title, price, currency) from a card's content-desc.

    Vinted example:
    "Apple Iphone 12 Pro 256GB ..., Značka: Apple, Model: iPhone 12 Pro,
     Stav: Velmi dobrý, 3954.14 Kč, 4169.85 Kč. Oblíbeno 16 krát..."

    Kleinanzeigen example:
    "iPhone 13 Pro Max 1TB Graphite.\\nPreis 549 €.\\nTOP.\\nObergiesing.\\n3"
    """
    # Normalize HTML entities
    desc = desc.replace("&#10;", "\n").replace("&amp;", "&")

    # Price: first number + KNOWN currency code. A bare [A-Z]{3} match in the
    # title (e.g. "RTX 3060 TUF") is a false positive — the known-currency
    # match wins; the first match is the fallback when none is known.
    price_matches = list(_PRICE_RE.finditer(desc))
    price_match = next((m for m in price_matches if m.group(2) in _KNOWN_CURRENCIES), None)
    if price_match is None and price_matches:
        price_match = price_matches[0]
    raw_price = price_match.group(1) if price_match else ""
    # Normalize: Czech format "3.954,14" → "3954.14"; plain "3954.14" stays
    if raw_price:
        if "," in raw_price:
            raw_price = raw_price.replace(".", "").replace(",", ".")
        price = raw_price
    else:
        price = ""
    currency = price_match.group(2) if price_match else ""

    # Title: everything before the first meta-key or "Preis" segment
    # Kleinanzeigen: "Title.\nPreis..."
    preis_match = re.search(r"\n\s*Preis\s", desc)
    if preis_match:
        title = desc[: preis_match.start()].strip().rstrip(".")
    else:
        # Vinted: "Title, Značka:..."
        meta_match = _META_KEYS.search(desc)
        if meta_match:
            title = desc[: meta_match.start()].strip()
        else:
            # Fallback: first comma or newline separated segment
            title = desc.split(",")[0].split("\n")[0].strip().rstrip(".")

    return title, price, currency
