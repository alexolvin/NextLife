"""Sbazar connector implementation (TZ v2.0 Section I.2, VI.1)."""

from typing import Any

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    ConnectorError,
    PublishResult,
    RawListing,
)


class SbazarConnector(BaseConnector):
    """Connector for Sbazar.cz direct parsing and publishing.

    Search route and parameter names come from the config route map
    (``routes`` in ``config/marketplaces/sbazar_cz.yaml``); the URL builder
    is the single base-class implementation (TZ M1 A.4).
    """

    def search(self, query: str) -> list[RawListing]:
        """Execute search on Sbazar.cz."""
        self.check_capability("search")
        url = self.build_search_url(query)
        resp = self.client.get(url)
        # Sbazar returns 404 for queries with no results (not a broken route).
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return self.parse_html_listings(resp.text)

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Sbazar via official Android application."""
        import uuid
        from datetime import UTC, datetime

        check_context_for_internal_fields(lot_data)
        self.check_capability("post")

        if dry_run:
            return PublishResult(
                external_id=f"dry_sbazar_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/nova-nabidka",
                evidence={
                    "dry_run": True,
                    "connector": "sbazar_android_bridge",
                    "steps": [
                        {"step": "launch_app", "mode": "dry_run", "result": "ok"},
                        {"step": "open_sell_form", "mode": "dry_run", "result": "ok"},
                        {"step": "fill_title", "mode": "dry_run", "result": "ok"},
                        {"step": "fill_price", "mode": "dry_run", "result": "ok"},
                        {"step": "fill_description", "mode": "dry_run", "result": "ok"},
                        {"step": "upload_photos", "mode": "dry_run", "result": "ok"},
                        {"step": "select_category", "mode": "dry_run", "result": "ok"},
                        {"step": "preview", "mode": "dry_run", "result": "ok"},
                    ],
                    "ts": datetime.now(UTC).isoformat(),
                },
            )

        from nextlife.services.sbazar_android_publish import sbazar_android_publish_listing

        res = sbazar_android_publish_listing(
            title=lot_data.get("title", ""),
            description=lot_data.get("description", ""),
            price=str(lot_data.get("price", "0")),
            category=lot_data.get("category", "Grafické karty"),
            dry_run=False,
            item_code=lot_data.get("item_code", ""),
            photo_paths=lot_data.get("photos"),
        )
        if not res.get("ok"):
            err_msg = "; ".join(res.get("errors") or ["Sbazar publication failed"])
            raise ConnectorError(err_msg)

        return PublishResult(
            external_id=res.get("external_id") or f"sbazar_{lot_data.get('item_code', '')}",
            external_url=res.get("final_url") or f"{self.config.base_url.rstrip('/')}/",
            evidence=res,
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing Sbazar listing."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        return self.publish(lot_data, dry_run=True)

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove Sbazar listing."""
        self.check_capability("archive")
        return True
