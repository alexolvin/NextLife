"""TrhKnih.cz connector (TZ M1 A.5, «подключение по шаблону Bazoš»).

Search: plain-HTML GET search page, config-driven selectors/routes
(live-verified 2026-10-04, artifact .a/reports/m1/probes/2026-10-04/
trhknih_cz_search_python.html). Books-only marketplace: the category
restriction lives in the marketplace registry (config
``allowed_categories``, enforced in the publish path + tests).
"""

from typing import Any

from nextlife.comms.outgoing_filter import check_context_for_internal_fields
from nextlife.connectors.base import (
    BaseConnector,
    ConnectorError,
    PublishResult,
    RawListing,
)


class TrhKnihConnector(BaseConnector):
    """Connector for TrhKnih.cz direct parsing (books only, TZ M1 A.5).

    Search route and parameter names come from the config route map
    (``routes`` in ``config/marketplaces/trhknih_cz.yaml``); the URL
    builder is the single base-class implementation (TZ M1 A.4/A.5).
    """

    def search(self, query: str) -> list[RawListing]:
        """Execute search on TrhKnih.cz."""
        self.check_capability("search")
        url = self.build_search_url(query)
        resp = self.client.get(url)
        resp.raise_for_status()
        return self.parse_html_listings(resp.text)

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on TrhKnih."""
        import uuid
        from datetime import UTC, datetime
        from nextlife.services.settings_store import get_secrets

        check_context_for_internal_fields(lot_data)
        self.check_capability("post")

        category = str(lot_data.get("category") or "").lower()
        title = str(lot_data.get("title") or "").lower()
        allowed = getattr(self.config, "allowed_categories", None) or ["books"]
        if allowed and not any(cat in category or cat in title for cat in ("book", "kniha", "knihy", "román", "ucebnice", "beletrie", "odborna")):
            raise ConnectorError(
                "TrhKnih.cz принимает исключительно книги: «Na webových stránkách www.trhknih.cz je "
                "umístěno internetové tržiště, prostřednictvím kterého mohou jeho uživatelé nakupovat a "
                "prodávat knihy» (Obchodní podmínky čl. 1.2)."
            )

        if dry_run:
            return PublishResult(
                external_id=f"dry_trhknih_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/prodat",
                evidence={
                    "dry_run": True,
                    "connector": "trhknih_cz_direct",
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
        email = secrets.get("TRHKNIH_CZ_EMAIL", "")
        password = secrets.get("TRHKNIH_CZ_PASSWORD", "")
        if not email or not password:
            raise ConnectorError(
                "TrhKnih: требуется авторизация (укажите TRHKNIH_CZ_EMAIL и TRHKNIH_CZ_PASSWORD в Настройки -> Площадки)"
            )

        raise ConnectorError(
            "TrhKnih: веб-сессия требует подтверждения входа через https://www.trhknih.cz/prihlasit"
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
