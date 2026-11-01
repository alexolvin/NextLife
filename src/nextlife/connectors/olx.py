"""OLX.pl connector (TZ-connectors-add-4).

Search: GET of the search page (``/oferty/q-{query}/``) and extraction of the
embedded ``window.__PRERENDERED_STATE__`` JSON (a JSON-escaped string), then
parsing ``listing.listing.ads[]``. Each ad carries ``id``/``title``/``url``/
``price.regularPrice``/``photos[]``/``location.pathName``/``createdTime``/
``itemCondition``/``isBusiness``.

Transport: olx.pl sits behind protection that 403s the default httpx TLS
fingerprint (verified 2026-10-22 from a datacenter IP: plain httpx → 403,
curl_cffi chrome impersonation → 200). The httpx client is therefore replaced
with a CffiClient adapter (same pattern as annonce.cz). The response body is
decoded as UTF-8 explicitly (curl_cffi mis-detects the charset, producing
mojibake in Polish titles otherwise).

The partner API is NOT used for reading (TZ §3.1 honesty): data comes from
the embedded ``__PRERENDERED_STATE__`` JSON in the server-rendered page.
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

# The prerendered state is a JSON-escaped string: window.__PRERENDERED_STATE__="..."
_PRERENDERED_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*(".*?")\s*;', re.S)


def extract_prerendered_state(html: str) -> dict[str, Any]:
    """Extract and parse the ``__PRERENDERED_STATE__`` JSON from OLX HTML.

    Returns an empty dict when the marker is absent (structure change — the
    caller reports it honestly rather than fabricating results).
    """
    m = _PRERENDERED_RE.search(html)
    if not m:
        return {}
    try:
        inner = json.loads(m.group(1))  # the JSON string
        state = json.loads(inner)  # the object
    except (json.JSONDecodeError, TypeError):
        return {}
    return state if isinstance(state, dict) else {}


def _parse_ads(state: dict[str, Any], base_url: str, currency: str) -> list[RawListing]:
    """Parse ``listing.listing.ads[]`` into RawListing items."""
    listing = (state.get("listing") or {}).get("listing") or {}
    items: list[RawListing] = []
    for ad in listing.get("ads") or []:
        ad_id = ad.get("id")
        title = (ad.get("title") or "").strip()
        url = ad.get("url") or ""
        if not ad_id or not title or not url:
            continue
        price_obj = ad.get("price") or {}
        price: Decimal | None = None
        if price_obj.get("free"):
            price = Decimal(0)
        elif not price_obj.get("exchange"):
            regular = price_obj.get("regularPrice") or {}
            amount = regular.get("value")
            if amount is not None:
                try:
                    price = Decimal(str(amount))
                except Exception:
                    price = None
        if price is None:
            continue  # exchange-only / no price -> skipped (clean data)
        photos = ad.get("photos") or []
        image_url = photos[0] if photos else ""
        location = ((ad.get("location") or {}).get("pathName")) or ""
        posted_at: datetime | None = None
        created = ad.get("createdTime")
        if created:
            try:
                posted_at = datetime.fromisoformat(created).astimezone(UTC)
            except ValueError:
                posted_at = None
        items.append(
            RawListing(
                title=title,
                price=price,
                currency=(price_obj.get("regularPrice") or {}).get("currencyCode") or currency,
                external_url=url,
                condition_text=ad.get("itemCondition") or None,
                posted_at=posted_at,
                raw={
                    "ad_id": ad_id,
                    "location": location,
                    "is_business": bool(ad.get("isBusiness")),
                    "image_url": image_url,
                    "photo_count": len(photos),
                },
            )
        )
    return items


class OlxConnector(BaseConnector):
    """Connector for OLX.pl: search via the embedded ``__PRERENDERED_STATE__``
    JSON. Transport is curl_cffi (chrome impersonation) — see module docstring."""

    def __init__(self, config: Any, client: Any = None) -> None:
        super().__init__(config, client)
        if client is None:
            from nextlife.connectors.cffi_client import CffiClient

            self.client = CffiClient()  # type: ignore[assignment]

    def _get(self, url: str) -> tuple[int | None, str, str, str | None]:
        """GET a URL; returns (status, final_url, html, network_error).

        The body is decoded as UTF-8 explicitly (curl_cffi mis-detects the
        charset for OLX, producing mojibake otherwise).
        """
        try:
            resp = self.client.get(url)
            return resp.status_code, str(resp.url), resp.content.decode("utf-8"), None
        except httpx.HTTPError as exc:
            return None, url, "", f"{type(exc).__name__}: {exc}"

    def _parse_html(self, html: str) -> list[RawListing]:
        state = extract_prerendered_state(html)
        return _parse_ads(state, self.config.base_url, self.config.currency)

    def search(self, query: str) -> list[RawListing]:
        """Execute search on OLX.pl."""
        self.check_capability("search")
        url = self.build_search_url(query)
        status, _final, html, network_error = self._get(url)
        if network_error is not None:
            raise ConnectorError(f"OLX search failed: {network_error}")
        if status != 200:
            raise ConnectorError(f"OLX search failed: HTTP {status}")
        return self._parse_html(html)

    def search_with_html(self, html_fixture: str) -> list[RawListing]:
        """Parse listings from a recorded fixture HTML."""
        return self._parse_html(html_fixture)

    def probe_search(self, query: str) -> SearchProbe:
        """One live search probe (TZ M1 D.1): GET + parse the embedded JSON."""
        self.check_capability("search")
        url = self.build_search_url(query)
        status, final_url, html, network_error = self._get(url)
        state = extract_prerendered_state(html) if status == 200 else {}
        listing = (state.get("listing") or {}).get("listing") or {}
        matched = len(listing.get("ads") or [])
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
        """Publish lot on OLX — not implemented (TZ-connectors-add-4 non-goal:
        no posting; the partner API is posting-only and is not used). Ч-5."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        raise ConnectorError(
            "OLX publish not implemented (TZ-connectors-add-4: posting is a "
            "non-goal; the partner API is not used for reading/writing). "
            "capabilities.post is false."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError("OLX update not implemented. capabilities.edit is false.")

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove listing — not implemented (see publish)."""
        self.check_capability("archive")
        raise ConnectorError("OLX unpublish not implemented. capabilities.archive is false.")
