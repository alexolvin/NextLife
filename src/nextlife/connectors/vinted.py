"""Vinted (vinted.cz) web connector — skeleton (owner decision 2026-10-10).

Vinted listing goes through the web (vinted.cz in Chrome), not the Android
app: the app's form fields (title/price/description) are Compose/Bloom with
no accessibility and cannot be focused via ADB tap. Search stays on the
ANDROID channel (phone broker); only the publish path is routed here.

This is a SKELETON: the dry-run path runs the publish-flow steps as no-op
stubs (no network, no browser) and returns a controlled result; the live path
raises explicitly (form navigation, photo upload and submit are not built yet).
"""

import os
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    CapabilityNotSupported,
    PublishResult,
    RawListing,
)

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

    def search(self, query: str) -> list[RawListing]:
        """Vinted search runs on the ANDROID channel (phone broker); the web
        catalog is DataDome-protected for account operations."""
        raise CapabilityNotSupported(
            "vinted: web search not implemented; search runs on the ANDROID channel."
        )

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
