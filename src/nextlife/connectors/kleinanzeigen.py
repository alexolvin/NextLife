"""Kleinanzeigen.de connector (TZ-connectors-add-4).

Search: plain-HTML GET of the search page (``/s-{query}/k0``), parsing the
``<article data-adid>`` cards. Each card carries the title (``h3``), the price
(a ``text-title3 font-strong`` paragraph reading ``N € [VB]``), the location
(a ``NNNNN City`` span element) and the thumbnail (``img`` / ld+json
``contentUrl``). Search cards expose no date, so ``posted_at`` stays None
(Ч-1: never fabricated).

Live-verified 2026-10-22 from a datacenter IP: plain httpx returns 200 with
27 cards (no TLS impersonation needed, unlike annonce.cz). The site is among
the most protected in the list (TZ §3.2) — a 403/challenge from a datacenter
IP is expected on other networks; the connector degrades honestly (HTTP error
/ structure_changed) rather than fabricating results.
"""

import re
from decimal import Decimal
from typing import Any

import httpx
from bs4 import BeautifulSoup

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    ConnectorError,
    PublishResult,
    RawListing,
    SearchProbe,
)

# German price: '.' is the thousands separator, ',' the decimal point
# («1.000 €» = 1000, «5,00 €» = 5.0). A bare «VB» (Verhandlungsbasis) has no
# numeric amount.
_GERMAN_PRICE_RE = re.compile(r"([\d.,]+)\s*€")
# Location span: a 4-5 digit postal code followed by a city name.
_LOCATION_RE = re.compile(r"^\d{4,5}\s+\S")


def _parse_german_price(text: str) -> Decimal | None:
    """Parse a German price string into a Decimal.

    Returns None when there is no positive numeric amount (e.g. a bare
    «VB» or «Preis auf Anfrage»).
    """
    m = _GERMAN_PRICE_RE.search(text)
    if not m:
        return None
    num = m.group(1).replace(".", "").replace(",", ".")
    if not num or num == ".":
        return None
    try:
        value = Decimal(num)
    except Exception:
        return None
    return value if value > 0 else None


class KleinanzeigenConnector(BaseConnector):
    """Connector for Kleinanzeigen.de: search via HTML ``<article>`` cards.

    The card markup is specific (``data-adid``/``data-href`` attributes,
    German price format, ld+json image), so the connector uses a dedicated
    parser instead of the base selector-driven one. ``access_mode`` stays
    ANDROID (the phone-broker channel config) — the web connector is the
    working search channel, mirroring the Vinted pattern.
    """

    def _parse_cards(self, html: str) -> list[RawListing]:
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawListing] = []
        for card in soup.select("article[data-adid]"):
            href = card.get("data-href") or ""
            if not href:
                a = card.select_one("h3 a[href]")
                href = a["href"] if a else ""
            if not href:
                continue
            h3 = card.select_one("h3")
            title = h3.get_text(" ", strip=True) if h3 else ""
            if not title:
                continue
            # Price: the paragraph with the title3/font-strong classes (the
            # bare «VB» card has no numeric amount -> skipped, clean data).
            price: Decimal | None = None
            price_p = card.select_one("p.text-title3.font-strong")
            if price_p:
                price = _parse_german_price(price_p.get_text(" ", strip=True))
            if price is None:
                continue
            location = ""
            for span in card.select("span"):
                t = span.get_text(" ", strip=True)
                if _LOCATION_RE.match(t):
                    location = t
                    break
            img = card.select_one("img")
            image_url = (img.get("src") or img.get("data-src") or "") if img else ""
            url = (
                f"{self.config.base_url.rstrip('/')}{href}"
                if href.startswith("/")
                else href
            )
            items.append(
                RawListing(
                    title=title,
                    price=price,
                    currency=self.config.currency,
                    external_url=url,
                    condition_text=None,
                    posted_at=None,
                    raw={
                        "ad_id": card.get("data-adid"),
                        "location": location,
                        "image_url": image_url,
                    },
                )
            )
        return items

    def search(self, query: str) -> list[RawListing]:
        """Execute search on Kleinanzeigen.de."""
        self.check_capability("search")
        url = self.build_search_url(query)
        resp = self.client.get(url)
        resp.raise_for_status()
        return self._parse_cards(resp.text)

    def search_with_html(self, html_fixture: str) -> list[RawListing]:
        """Parse listings from a recorded fixture HTML."""
        return self._parse_cards(html_fixture)

    def probe_search(self, query: str) -> SearchProbe:
        """One live search probe (TZ M1 D.1): GET + parse the article cards."""
        self.check_capability("search")
        url = self.build_search_url(query)
        final_url = ""
        text = ""
        status: int | None = None
        network_error: str | None = None
        try:
            resp = self.client.get(url)
            final_url = str(resp.url)
            status = resp.status_code
            text = resp.text
        except httpx.HTTPError as exc:
            network_error = f"{type(exc).__name__}: {exc}"
        items: list[RawListing] = []
        matched = 0
        if status == 200:
            soup = BeautifulSoup(text, "html.parser")
            matched = len(soup.select("article[data-adid]"))
            items = self._parse_cards(text)
        return SearchProbe(
            http_status=status,
            requested_url=url,
            final_url=final_url,
            raw_text=text,
            items=items,
            matched=matched,
            network_error=network_error,
            date_absent_on_site=True,
        )

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Kleinanzeigen — not implemented (TZ-connectors-add-4
        non-goal: no posting). Must NOT return a fabricated PublishResult (Ч-5)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        raise ConnectorError(
            "Kleinanzeigen publish not implemented (TZ-connectors-add-4: "
            "posting is a non-goal). capabilities.post is false."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError(
            "Kleinanzeigen update not implemented. capabilities.edit is false."
        )

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove listing — not implemented (see publish)."""
        self.check_capability("archive")
        raise ConnectorError(
            "Kleinanzeigen unpublish not implemented. capabilities.archive is false."
        )
