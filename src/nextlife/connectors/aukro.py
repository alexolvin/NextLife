"""Aukro connector implementation (TZ v2.0 Section I.2, VI.1; M1 A.3 repair).

Search (TZ M1 A.3): Aukro's HTML search endpoint
``/vysledky-vyhledavani`` serves a JS SPA shell to plain HTTP — the SPA
itself calls the site-internal JSON API
``POST /backend-web/api/offers/searchItemsCommon`` (discovered in the SPA
bundles ``main-X6MWIO32.js`` / ``chunk-DJL7IUES.js``; live-verified
2026-10-04, artifact ``.a/reports/m1/probes/2026-10-04/
aukro_cz_search_api_probe.json``). Queries that map to a category come
back as ``{"content": [], "redirectUrl": "/<category-seo-url>"}`` — the
SPA follows that with ``itemSearchFilter.categorySeoUrl``; the connector
does the same. This is still the DIRECT channel (plain HTTP, no auth).

Publish: the FEED channel — an XML feed (``aukro-feed.xml``) that Aukro's
Manažer prodeje (mp.aukro.cz) pulls by URL. NextLife does not log into
Aukro; the owner presses «Vystavit» in MP as the final approval. The
Aukro API is legal-entity-only and the web login is behind Cloudflare
Turnstile, so both DIRECT and API publish are not used (removed 2026-10-24).
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    CapabilityNotSupported,
    ConnectorError,
    PublishResult,
    RawListing,
    SearchProbe,
)

SEARCH_API_PATH = "/backend-web/api/offers/searchItemsCommon"
SEARCH_PAGE_SIZE = 20
# Aukro attribute "Stav zboží" (condition) — live-verified attributeId.
CONDITION_ATTRIBUTE_ID = 48


def _parse_item(item: dict[str, Any], base_url: str) -> RawListing | None:
    """One Aukro search item -> RawListing. Returns None when unusable.

    Ч-1: posted_at only from ``startingTime`` present in the API payload;
    price is the current card price (current bid for auctions, buy-now /
    asking price otherwise) — exactly what the site card shows.
    """
    item_id = item.get("itemId")
    seo_url = item.get("seoUrl")
    name = (item.get("itemName") or "").strip()
    price_obj = item.get("price") or {}
    amount = price_obj.get("amount")
    currency = price_obj.get("currency") or ""
    if not item_id or not seo_url or not name or not amount:
        return None
    try:
        price = Decimal(str(amount))
    except Exception:
        return None
    if price <= 0:
        return None

    posted_at: datetime | None = None
    started = item.get("startingTime")
    if started:
        try:
            posted_at = datetime.fromisoformat(started).astimezone(UTC)
        except ValueError:
            posted_at = None

    condition_text: str | None = None
    for attr in item.get("attributes") or []:
        if attr.get("attributeId") == CONDITION_ATTRIBUTE_ID:
            condition_text = attr.get("attributeValue") or None
            break

    # Thumbnail: the API carries it in ``titleImageUrl`` (or ``titleImage.url``).
    image_url = item.get("titleImageUrl") or ""
    if not image_url:
        title_image = item.get("titleImage") or {}
        image_url = title_image.get("url") or ""

    return RawListing(
        title=name,
        price=price,
        currency=currency,
        external_url=f"{base_url.rstrip('/')}/{seo_url}-{item_id}",
        condition_text=condition_text,
        posted_at=posted_at,
        raw={
            "item_id": item_id,
            "auction": bool(item.get("auction")),
            "buy_now_active": bool(item.get("buyNowActive")),
            "ending_time": item.get("endingTime"),
            "image_url": image_url,
        },
    )


class AukroConnector(BaseConnector):
    """Connector for Aukro.cz: search via the site-internal JSON API."""

    def _search_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}{SEARCH_API_PATH}?page=0&size={SEARCH_PAGE_SIZE}"

    def _post_filter(self, body: dict[str, Any]) -> tuple[int | None, str, str, str, str | None]:
        """POST one itemSearchFilter; returns (status, requested, final, text, network_error)."""
        url = self._search_url()
        try:
            resp = self.client.post(url, json=body)
            return resp.status_code, url, str(resp.url), resp.text, None
        except Exception as exc:  # httpx.HTTPError and friends
            return None, url, url, "", f"{type(exc).__name__}: {exc}"

    def probe_search(self, query: str) -> SearchProbe:
        """Live search probe over the site JSON API (TZ M1 D.1, A.3).

        A query that the backend maps to a category/landing page answers
        ``{"content": [], "redirectUrl": "/<seo-url>"}`` (Aukro's intended
        SEO behavior — the SPA follows it too). The connector follows the
        redirect via ``categorySeoUrl``; if the follow-up is also empty
        the honest result is «empty», not a fabricated one.
        """
        self.check_capability("search")
        status, requested, final_url, text, network_error = self._post_filter({"text": query.strip()})
        data: dict[str, Any] = {}
        if status == 200 and network_error is None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {}
            redirect = data.get("redirectUrl")
            if redirect and not data.get("content"):
                # Aukro now routes popular queries to landing pages
                # (``/lp/...``), which are NOT valid ``categorySeoUrl`` —
                # following them 404s ("Category page does not exist"). Only
                # follow real category SEO URLs; a landing-page redirect
                # means "no direct results for this query", not an error.
                if not str(redirect).strip("/").startswith("lp/"):
                    status2, requested2, final_url2, text2, network_error2 = self._post_filter(
                        {"categorySeoUrl": str(redirect).strip("/")}
                    )
                    if network_error2 is None:
                        status, requested, final_url, text, network_error = (
                            status2,
                            requested2,
                            final_url2,
                            text2,
                            network_error2,
                        )
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            data = {}

        items: list[RawListing] = []
        matched = 0
        if status == 200 and network_error is None:
            content = data.get("content") or []
            matched = len(content)
            for raw in content:
                parsed = _parse_item(raw, self.config.base_url)
                if parsed is not None:
                    items.append(parsed)
        # The API always carries startingTime per item; when an item lacks
        # it, posted_at stays None and the canary schema check fails
        # honestly (schema_drift: field=date), never a fabricated date.
        return SearchProbe(
            http_status=status,
            requested_url=requested,
            final_url=final_url,
            raw_text=text,
            items=items,
            matched=matched,
            network_error=network_error,
            date_absent_on_site=False,
        )

    def search(self, query: str) -> list[RawListing]:
        """Execute search on Aukro.cz (site JSON API, A.3)."""
        probe = self.probe_search(query)
        if probe.network_error is not None:
            raise ConnectorError(f"Aukro search failed: {probe.network_error}")
        if probe.http_status != 200:
            raise ConnectorError(f"Aukro search failed: HTTP {probe.http_status}")
        return probe.items

    def search_with_json(self, json_fixture: str) -> list[RawListing]:
        """Parse listings from a recorded JSON API response (fixture)."""
        data = json.loads(json_fixture)
        items: list[RawListing] = []
        for raw in data.get("content") or []:
            parsed = _parse_item(raw, self.config.base_url)
            if parsed is not None:
                items.append(parsed)
        return items

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Aukro.

        Not via this connector: Aukro publish is the FEED channel — an XML
        feed (``aukro-feed.xml``) that Aukro's Manažer prodeje pulls by URL.
        The Aukro API is legal-entity-only and the web login is behind
        Cloudflare Turnstile, so DIRECT/API publish are not used (removed
        2026-10-24). This connector is search-only.
        """
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        raise CapabilityNotSupported(
            f"Marketplace '{self.key}' does not support 'post' capability."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing Aukro listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError(
            "Aukro update not implemented: requires the owner's Aukro API "
            "key (TZ M1 A.3). capabilities.edit is false."
        )

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove Aukro listing — not implemented (see publish)."""
        self.check_capability("archive")
        raise ConnectorError(
            "Aukro unpublish not implemented: requires the owner's Aukro "
            "API key (TZ M1 A.3). capabilities.archive is false."
        )
