"""Willhaben.at connector (TZ-connectors-add-4).

Search: GET of the category search page
(``/iad/kaufen-und-verkaufen/marktplatz/?keyword={query}``) and extraction of
the server-rendered ``__NEXT_DATA__`` JSON, then parsing
``props.pageProps.searchResult.advertSummaryList.advertSummary[]``. Each ad
carries its fields in an ``attributes.attribute[]`` name/value list:
``HEADING`` (title), ``PRICE/AMOUNT`` (numeric, dot decimal), ``SEO_URL``
(ad path), ``LOCATION``/``POSTCODE``/``STATE``, ``PUBLISHED_String`` (ISO 8601
date), ``ISPRIVATE`` (1 = private, 0 = business), ``ORGNAME`` (seller). Images
live in ``advertImageList.advertImage[].mainImageUrl`` (``cache.willhaben.at``).

Transport: plain httpx returns 200 from a datacenter IP (verified 2026-10-22)
— no TLS impersonation needed. The internal mobile API is NOT used (TZ §3.3
honesty: it is SSL-pinned); data comes from the embedded ``__NEXT_DATA__``
JSON in the server-rendered page.

Note: the SSR page is only populated when the search URL targets a category
(``/marktplatz/``); the bare ``/iad/kaufen-und-verkaufen/?keyword=`` page
returns the whole-market shell with an empty ``advertSummary``.
"""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    ConnectorError,
    PublishResult,
    RawListing,
    SearchProbe,
)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def extract_next_data(html: str) -> dict[str, Any]:
    """Extract and parse the ``__NEXT_DATA__`` JSON from willhaben HTML.

    Returns an empty dict when the marker is absent (structure change — the
    caller reports it honestly rather than fabricating results).
    """
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _attr_map(ad: dict[str, Any]) -> dict[str, str]:
    """Flatten an ad's ``attributes.attribute[]`` name/value list to a dict."""
    attrs = ((ad.get("attributes") or {}).get("attribute")) or []
    out: dict[str, str] = {}
    for a in attrs:
        name = a.get("name")
        values = a.get("values") or []
        if name and values:
            out[name] = str(values[0])
    return out


def _parse_adverts(next_data: dict[str, Any], base_url: str, currency: str) -> list[RawListing]:
    """Parse ``searchResult.advertSummaryList.advertSummary[]`` into RawListing."""
    sr = ((next_data.get("props") or {}).get("pageProps") or {}).get("searchResult") or {}
    asl = (sr.get("advertSummaryList") or {}).get("advertSummary") or []
    items: list[RawListing] = []
    for ad in asl:
        ad_id = str(ad.get("id") or "")
        at = _attr_map(ad)
        title = (at.get("HEADING") or ad.get("description") or "").strip()
        if not ad_id or not title:
            continue
        price: Decimal | None = None
        amount = at.get("PRICE/AMOUNT")
        if amount:
            try:
                price = Decimal(amount.replace(".", "").replace(",", ".")) if "," in amount else Decimal(amount)
            except Exception:
                price = None
        if price is None:
            continue  # no price -> skipped (clean data)
        # Web URL from the SEO path (the selfLink is an API endpoint, not the
        # public ad page).
        seo = at.get("SEO_URL") or ""
        url = f"{base_url.rstrip('/')}/iad/{seo}" if seo else ""
        if not url:
            continue
        posted_at: datetime | None = None
        published = at.get("PUBLISHED_String")
        if published:
            try:
                posted_at = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                posted_at = None
        location = at.get("LOCATION") or ""
        is_private = at.get("ISPRIVATE") == "1"
        seller_type = "private" if is_private else "business"
        images = ((ad.get("advertImageList") or {}).get("advertImage")) or []
        image_url = images[0].get("mainImageUrl") or images[0].get("thumbnailImageUrl") or "" if images else ""
        items.append(
            RawListing(
                title=title,
                price=price,
                currency=currency,
                external_url=url,
                condition_text=None,
                posted_at=posted_at,
                raw={
                    "ad_id": ad_id,
                    "location": location,
                    "seller_type": seller_type,
                    "seller": at.get("ORGNAME") or None,
                    "image_url": image_url,
                },
            )
        )
    return items


class WillhabenConnector(BaseConnector):
    """Connector for Willhaben.at: search via the embedded ``__NEXT_DATA__``
    JSON (category search page). See module docstring for the URL contract."""

    def _get(self, url: str) -> tuple[int | None, str, str, str | None]:
        try:
            resp = self.client.get(url)
            return resp.status_code, str(resp.url), resp.text, None
        except httpx.HTTPError as exc:
            return None, url, "", f"{type(exc).__name__}: {exc}"

    def _parse_html(self, html: str) -> list[RawListing]:
        return _parse_adverts(extract_next_data(html), self.config.base_url, self.config.currency)

    def search(self, query: str) -> list[RawListing]:
        """Execute search on Willhaben.at."""
        self.check_capability("search")
        url = self.build_search_url(query)
        status, _final, html, network_error = self._get(url)
        if network_error is not None:
            raise ConnectorError(f"Willhaben search failed: {network_error}")
        if status != 200:
            raise ConnectorError(f"Willhaben search failed: HTTP {status}")
        return self._parse_html(html)

    def search_with_html(self, html_fixture: str) -> list[RawListing]:
        """Parse listings from a recorded fixture HTML."""
        return self._parse_html(html_fixture)

    def probe_search(self, query: str) -> SearchProbe:
        """One live search probe (TZ M1 D.1): GET + parse the embedded JSON."""
        self.check_capability("search")
        url = self.build_search_url(query)
        status, final_url, html, network_error = self._get(url)
        next_data = extract_next_data(html) if status == 200 else {}
        sr = ((next_data.get("props") or {}).get("pageProps") or {}).get("searchResult") or {}
        asl = (sr.get("advertSummaryList") or {}).get("advertSummary") or []
        matched = len(asl)
        items = self._parse_html(html) if status == 200 else []
        return SearchProbe(
            http_status=status,
            requested_url=url,
            final_url=final_url,
            raw_text=html,
            items=items,
            matched=matched,
            network_error=network_error,
            date_absent_on_site=False,
        )

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Willhaben — not implemented (TZ-connectors-add-4
        non-goal: no posting; the internal mobile API is SSL-pinned and is not
        used). Ч-5."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        raise ConnectorError(
            "Willhaben publish not implemented (TZ-connectors-add-4: posting "
            "is a non-goal; the SSL-pinned mobile API is not used). "
            "capabilities.post is false."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError("Willhaben update not implemented. capabilities.edit is false.")

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove listing — not implemented (see publish)."""
        self.check_capability("archive")
        raise ConnectorError("Willhaben unpublish not implemented. capabilities.archive is false.")
