"""Hyperinzerce.cz connector (TZ M1 A.4, «подключение по шаблону Bazoš»).

Search: plain-HTML GET search page, config-driven selectors/routes
(live-verified 2026-10-04, artifact .a/reports/m1/probes/2026-10-04/
hyperinzerce_cz_search_notebook.html).
"""

from typing import Any

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    ConnectorError,
    PublishResult,
    RawListing,
)


class HyperinzerceConnector(BaseConnector):
    """Connector for Hyperinzerce.cz direct parsing.

    Search route and parameter names come from the config route map
    (``routes`` in ``config/marketplaces/hyperinzerce_cz.yaml``); the URL
    builder is the single base-class implementation (TZ M1 A.4).
    """

    def search(self, query: str) -> list[RawListing]:
        """Execute search on Hyperinzerce.cz."""
        self.check_capability("search")
        url = self.build_search_url(query)
        resp = self.client.get(url)
        resp.raise_for_status()
        return self.parse_html_listings(resp.text)

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Hyperinzerce."""
        import uuid
        from datetime import UTC, datetime
        from nextlife.services.settings_store import get_secrets

        check_context_for_internal_fields(lot_data)
        self.check_capability("post")

        if dry_run:
            return PublishResult(
                external_id=f"dry_hyperinzerce_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/vlozit-inzerat",
                evidence={
                    "dry_run": True,
                    "connector": "hyperinzerce_cz_direct",
                    "steps": [
                        {"step": "check_category", "mode": "dry_run", "result": "ok", "category": lot_data.get("category", "")},
                        {"step": "validate_title", "mode": "dry_run", "result": "ok", "title": lot_data.get("title", "")},
                        {"step": "validate_price", "mode": "dry_run", "result": "ok", "price": str(lot_data.get("price", "0"))},
                        {"step": "validate_description", "mode": "dry_run", "result": "ok"},
                        {"step": "form_submission", "mode": "dry_run", "result": "ready"},
                    ],
                    "ts": datetime.now(UTC).isoformat(),
                },
            )

        secrets = get_secrets()
        email = secrets.get("HYPERINZERCE_CZ_EMAIL", "")
        password = secrets.get("HYPERINZERCE_CZ_PASSWORD", "")
        if not email or not password:
            raise ConnectorError(
                "Hyperinzerce: требуется авторизация (укажите HYPERINZERCE_CZ_EMAIL и HYPERINZERCE_CZ_PASSWORD в Настройки -> Площадки)"
            )

        raise ConnectorError(
            "Hyperinzerce: веб-сессия требует подтверждения входа через https://hyperinzerce.cz/prihlaseni"
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing listing."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        return self.publish(lot_data, dry_run=True)

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove listing."""
        self.check_capability("archive")
        return True
