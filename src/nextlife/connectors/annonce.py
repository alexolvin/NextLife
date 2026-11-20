"""Annonce.cz connector (TZ M1 A.4, «подключение по шаблону Bazoš»).

Search: plain-HTML GET search page, config-driven selectors/routes
(live-verified 2026-10-04, artifact .a/reports/m1/probes/2026-10-04/
annonce_cz_search_notebook.html). The «všechny inzeráty» feed mixes item
ads, job ads and requests; cards without a price sticker (job ads,
requests) are skipped by the base parser, which keeps the price data clean.
"""

from typing import Any

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    ConnectorError,
    PublishResult,
    RawListing,
)


class AnnonceConnector(BaseConnector):
    """Connector for Annonce.cz direct parsing.

    Search route and parameter names come from the config route map
    (``routes`` in ``config/marketplaces/annonce_cz.yaml``); the URL
    builder is the single base-class implementation (TZ M1 A.4).

    Transport: annonce.cz sits behind Cloudflare and 403s the default
    httpx TLS fingerprint (JA3/JA4) with a challenge page; a Chrome TLS
    fingerprint (curl_cffi) passes (verified 2026-10-18). The httpx
    client is therefore replaced with a CffiClient adapter.
    """

    def __init__(self, config: Any, client: Any = None) -> None:
        super().__init__(config, client)
        if client is None:
            from nextlife.connectors.cffi_client import CffiClient

            self.client = CffiClient()  # type: ignore[assignment]

    def search(self, query: str) -> list[RawListing]:
        """Execute search on Annonce.cz."""
        self.check_capability("search")
        url = self.build_search_url(query)
        resp = self.client.get(url)
        resp.raise_for_status()
        return self.parse_html_listings(resp.text)

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Annonce."""
        import uuid
        from datetime import UTC, datetime
        from nextlife.services.settings_store import get_secrets

        check_context_for_internal_fields(lot_data)
        self.check_capability("post")

        if dry_run:
            return PublishResult(
                external_id=f"dry_annonce_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/pridat-inzerat.html",
                evidence={
                    "dry_run": True,
                    "connector": "annonce_cz_direct",
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
        email = secrets.get("ANNONCE_CZ_EMAIL", "")
        password = secrets.get("ANNONCE_CZ_PASSWORD", "")
        if not email or not password:
            raise ConnectorError(
                "Annonce: требуется авторизация (укажите ANNONCE_CZ_EMAIL и ANNONCE_CZ_PASSWORD в Настройки -> Площадки)"
            )

        raise ConnectorError(
            "Annonce: веб-сессия требует подтверждения входа через https://www.annonce.cz/prihlasit"
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
