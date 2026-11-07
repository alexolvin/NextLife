"""Vinted (vinted.cz) web connector — skeleton (owner decision 2026-10-10).

Vinted listing goes through the web (vinted.cz in Chrome), not the Android
app: the app's form fields (title/price/description) are Compose/Bloom with
no accessibility and cannot be focused via ADB tap. Search stays on the
ANDROID channel (phone broker); only the publish path is routed here.

This is a SKELETON: the dry-run path runs the publish-flow steps as no-op
stubs (no network, no browser) and returns a controlled result; the live path
raises explicitly (form navigation, photo upload and submit are not built yet).
"""

import logging
import os
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote_plus

import httpx

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    CapabilityNotSupported,
    ConnectorError,
    PublishResult,
    RawListing,
)

logger = logging.getLogger("nextlife.connectors.vinted")

_DRY_RUN_ENV = "NEXTLIFE_PUBLISH_DRY_RUN"

_VINTED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}

_ITEM_PATTERN = re.compile(
    r'data-testid=\"product-item-id-(?P<id>\d+)\".*?'
    r'<img[^>]+src=\"(?P<photo>[^\"]+)\"[^>]+alt=\"(?P<alt>[^\"]*)\".*?'
    r'<a[^>]+href=\"(?P<href>/items/\d+-[^\"]*)\"',
    re.DOTALL,
)

_PRICE_PATTERN = re.compile(r"(\d[\d\s\.]*)\s*(Kč|€|zł|EUR|CZK)", re.IGNORECASE)


def parse_vinted_catalog(html: str, base_url: str = "https://www.vinted.cz") -> list[RawListing]:
    """Parse Vinted server-rendered catalog HTML into RawListing items."""
    items: list[RawListing] = []
    seen_ids: set[str] = set()

    for m in _ITEM_PATTERN.finditer(html):
        item_id = m.group("id")
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        alt = m.group("alt") or ""
        href = m.group("href").split("?")[0]
        photo = m.group("photo")

        parts = [p.strip() for p in alt.split(",") if p.strip()]
        title = parts[0] if parts else f"Item {item_id}"

        brand = None
        condition = None
        for p in parts[1:]:
            p_low = p.lower()
            if p_low.startswith("značka:") or p_low.startswith("brand:"):
                brand = p.split(":", 1)[1].strip()
            elif p_low.startswith("stav:") or p_low.startswith("condition:"):
                condition = p.split(":", 1)[1].strip()

        price = Decimal("0")
        currency = "CZK"
        price_m = _PRICE_PATTERN.findall(alt)
        if price_m:
            raw_p, raw_c = price_m[0]
            try:
                price = Decimal(raw_p.replace(" ", "").replace(",", "."))
            except Exception:
                price = Decimal("0")
            c_upper = raw_c.upper()
            if c_upper in ("KČ", "CZK"):
                currency = "CZK"
            elif c_upper in ("€", "EUR"):
                currency = "EUR"
            elif c_upper in ("ZŁ", "PLN"):
                currency = "PLN"

        full_url = f"{base_url.rstrip('/')}{href}"
        items.append(
            RawListing(
                title=title,
                price=price,
                currency=currency,
                external_url=full_url,
                condition_text=condition,
                posted_at=None,
                raw={
                    "external_id": item_id,
                    "brand": brand,
                    "image_url": photo,
                    "photos": [photo] if photo else [],
                },
            )
        )
    return items

_DRY_RUN_ENV = "NEXTLIFE_PUBLISH_DRY_RUN"

# Publish-flow step contract: each name maps to one future Playwright action on
# vinted.cz. In dry-run every step is a no-op stub (recorded, no browser/network).
_PUBLISH_STEPS = (
    "open_sell_form",
    "fill_title",
    "fill_price",
    "fill_description",
    "upload_photos",
    "submit",
    "parse_result",
)


class VintedBrowserContext(Protocol):
    """Abstract browser context for the Vinted publish flow.

    A real implementation wraps a Playwright page on vinted.cz (not built yet).
    A mock implementation records the calls. In dry-run no context is needed.
    """

    def goto_sell_form(self) -> None: ...
    def fill_title(self, title: str) -> None: ...
    def fill_price(self, price: str) -> None: ...
    def fill_description(self, description: str) -> None: ...
    def upload_photos(self, photos: list[str]) -> None: ...
    def submit(self) -> None: ...
    def parse_result(self) -> dict[str, Any]: ...


class VintedPublishFlow:
    """Drives the Vinted publish steps through a browser context.

    When the context is None (dry-run) every step is a no-op stub; when a
    context is provided (live) each step calls the corresponding context
    method. The step order is the contract for the real Playwright
    implementation.
    """

    def __init__(self, context: VintedBrowserContext | None = None) -> None:
        self.context = context

    def run(self, lot_data: dict[str, Any]) -> list[dict[str, str]]:
        ctx = self.context
        mode = "live" if ctx is not None else "dry_run"
        steps: list[dict[str, str]] = []

        def record(name: str) -> None:
            steps.append({"step": name, "mode": mode, "result": "ok"})

        if ctx is not None:
            ctx.goto_sell_form()
        record("open_sell_form")
        if ctx is not None:
            ctx.fill_title(str(lot_data.get("title", "")))
        record("fill_title")
        if ctx is not None:
            ctx.fill_price(str(lot_data.get("price", "")))
        record("fill_price")
        if ctx is not None:
            ctx.fill_description(str(lot_data.get("description", "")))
        record("fill_description")
        if ctx is not None:
            ctx.upload_photos([str(p) for p in lot_data.get("photos", [])])
        record("upload_photos")
        if ctx is not None:
            ctx.submit()
        record("submit")
        if ctx is not None:
            ctx.parse_result()
        record("parse_result")
        return steps


def _dry_run_forced() -> bool:
    """The acceptance kill switch (owner, 2026-10-04): the env var forces
    dry-run on every publish — real publication is impossible."""
    return os.environ.get(_DRY_RUN_ENV, "").strip().lower() in ("1", "true", "yes")


class VintedConnector(BaseConnector):
    """Vinted (vinted.cz) web connector — skeleton (dry-run only)."""

    def search(self, query: str, limit: int = 20) -> list[RawListing]:
        """Search the public Vinted web catalog (vinted.cz)."""
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        base = self.config.base_url.rstrip("/") if self.config.base_url else "https://www.vinted.cz"
        url = f"{base}/catalog?search_text={quote_plus(clean_q)}"

        html = ""
        # Try curl_cffi for browser TLS fingerprint impersonation if available
        try:
            from curl_cffi import requests as cffi_requests
            resp = cffi_requests.get(url, headers=_VINTED_HEADERS, impersonate="chrome120", timeout=12)
            if resp.status_code == 200:
                html = resp.text
        except Exception as cffi_exc:
            logger.debug("Vinted curl_cffi search attempt failed: %s", cffi_exc)

        # Fallback to standard httpx if curl_cffi failed or was unavailable
        if not html:
            try:
                resp2 = httpx.get(url, headers=_VINTED_HEADERS, timeout=12.0, follow_redirects=True)
                if resp2.status_code == 200:
                    html = resp2.text
                elif resp2.status_code == 404:
                    return []
                else:
                    logger.warning("Vinted web search HTTP %s", resp2.status_code)
                    return []
            except Exception as exc:
                logger.error("Vinted web search request failed: %s", exc)
                return []

        listings = parse_vinted_catalog(html, base_url=base)
        return listings[:limit]

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish via the web. Skeleton: dry-run runs the publish-flow steps as
        no-op stubs (no browser, no network) and returns a controlled result;
        live publish raises explicitly."""
        check_context_for_internal_fields(lot_data)

        if dry_run or _dry_run_forced():
            return PublishResult(
                external_id=f"dry_vinted_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/item/dry-run",
                evidence={
                    "dry_run": True,
                    "connector": "vinted_web_skeleton",
                    "steps": VintedPublishFlow(context=None).run(lot_data),
                    "ts": datetime.now(UTC).isoformat(),
                },
            )

        raise CapabilityNotSupported(
            "vinted: live web publish not implemented (skeleton); form "
            "navigation, photo upload and submit are not built yet."
        )
