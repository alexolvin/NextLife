"""Bazoš connector implementation (TZ v2.0 Section I.2, VI.1)."""

from typing import Any

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    ConnectorError,
    PublishResult,
    RawListing,
)


class BazosConnector(BaseConnector):
    """Connector for Bazoš.cz direct parsing and publishing.

    Search route and parameter names come from the config route map
    (``routes`` in ``config/marketplaces/bazos_cz.yaml``); the URL builder
    is the single base-class implementation (TZ M1 A.4).
    """

    def search(self, query: str) -> list[RawListing]:
        """Execute search on Bazoš.cz."""
        self.check_capability("search")
        url = self.build_search_url(query)
        resp = self.client.get(url)
        resp.raise_for_status()
        return self.parse_html_listings(resp.text)

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Bazoš.

        Dry-run: returns a structured no-op (the wizard dry-run check).
        Live: not in this connector — the real publish flow is in
        ``bazos_publish.py`` (DIRECT web + SMS, human-in-the-loop).
        """
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        if dry_run:
            import uuid
            from datetime import UTC, datetime

            return PublishResult(
                external_id=f"dry_bazos_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/insert.php",
                evidence={
                    "dry_run": True,
                    "connector": "bazos_direct_skeleton",
                    "steps": [
                        {"step": "login_check", "mode": "dry_run", "result": "ok"},
                        {"step": "open_sell_form", "mode": "dry_run", "result": "ok"},
                        {"step": "fill_title", "mode": "dry_run", "result": "ok"},
                        {"step": "fill_price", "mode": "dry_run", "result": "ok"},
                        {"step": "fill_description", "mode": "dry_run", "result": "ok"},
                        {"step": "upload_photos", "mode": "dry_run", "result": "ok"},
                        {"step": "submit", "mode": "dry_run", "result": "ok"},
                        {"step": "parse_result", "mode": "dry_run", "result": "ok"},
                    ],
                    "ts": datetime.now(UTC).isoformat(),
                },
            )
        raise ConnectorError(
            "Bazoš live publish not in connector: use bazos_publish.py "
            "(DIRECT web + SMS, human-in-the-loop)."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing Bazoš listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError(
            "Bazoš update not implemented: requires the owner's Bazoš "
            "account (TZ M1 A.4). capabilities.edit is false."
        )

    def unpublish(self, external_id: str, title: str = "") -> bool:
        """Archive/remove Bazoš listing via Android PhoneBroker."""
        self.check_capability("archive")
        from nextlife.services.bazos_android_publish import bazos_android_delete_listing

        platform = "bazos_sk" if getattr(self, "key", "bazos_cz") == "bazos_sk" else "bazos_cz"
        res = bazos_android_delete_listing(
            title=title, external_id=external_id, platform=platform, dry_run=False
        )
        return bool(res.get("ok"))

    def relist(self, listing_data: dict[str, Any]) -> PublishResult:
        """Relist Bazoš listing via Android PhoneBroker."""
        self.check_capability("relist")
        from nextlife.services.bazos_android_publish import bazos_android_relist_listing

        platform = "bazos_sk" if getattr(self, "key", "bazos_cz") == "bazos_sk" else "bazos_cz"
        res = bazos_android_relist_listing(platform=platform, **listing_data)
        if not res.get("ok"):
            raise ConnectorError(f"Bazoš relist failed: {res.get('errors')}")
        return PublishResult(
            external_id=res.get("external_id") or "",
            url=res.get("final_url") or "",
            evidence_path=res.get("evidence") or "",
        )
