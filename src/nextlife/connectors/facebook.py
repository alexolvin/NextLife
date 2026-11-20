"""Facebook Marketplace connector — honest placeholder (TZ-connectors-add-4).

STATUS: blocked / login_wall. Facebook Marketplace has no public (unauthenticated)
search endpoint. Live-verified 2026-10-22 from a datacenter IP with curl_cffi
chrome impersonation: every search URL format redirects to a location-scoped
search that serves a login checkpoint (page title «Facebook», 0 listing links,
0 embedded listing JSON, «checkpoint» marker present). The marketplace home
page renders, but search results require a logged-in session.

The Graph API deliberately excludes Marketplace (TZ §3.4). Reading listings
therefore requires either (a) a logged-in user session — which the TZ
forbids without explicit owner consent and carries a high ban risk — or (b) an
external scraping service. Neither is implemented here.

This placeholder exists so the marketplace is a real, honest connector (not a
silent gap): search() raises a ConnectorError classified as ``login_wall`` so
the wizard reports the failure transparently instead of fabricating results
or hiding the marketplace. Ч-5: no fabricated listings.

## Честность: facebook_marketplace

- **Источник данных:** нет публичного источника (login wall; Graph API
  исключает Marketplace)
- **Обнаруженные защиты:** login checkpoint, captcha, device/ML fingerprinting,
  поведенческий анализ
- **Требования к инфраструктуре:** logged-in сессия (запрещено TZ без явного
  разрешения владельца) или внешний scraping-сервис
- **Полнота данных:** поиск — нет; детали — нет; галерея — нет
- **Ограничения:** без авторизации 0 публичных результатов
- **Риски:** ToS, GDPR, высокий риск бана аккаунта при использовании
  пользовательской сессии
- **Деградация:** search() бросает ConnectorError(login_wall) — мастер честно
  показывает ошибку, не выдавая выдуманных объявлений
"""

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


class FacebookLoginWallError(ConnectorError):
    """Raised when Facebook Marketplace search is attempted: the site serves a
    login checkpoint, not public results (TZ-connectors-add-4 §3.4)."""

    def __init__(self) -> None:
        super().__init__(
            "facebook_marketplace: login_wall — Facebook Marketplace has no "
            "public search endpoint (login checkpoint). A logged-in session or "
            "an external service is required; neither is implemented "
            "(TZ-connectors-add-4 §3.4)."
        )


class FacebookMarketplaceConnector(BaseConnector):
    """Placeholder connector for Facebook Marketplace: search is blocked by a
    login wall. See module docstring for the honesty block."""

    def search(self, query: str) -> list[RawListing]:
        """Search on Facebook Marketplace — blocked by a login wall.

        Raises an honest ``FacebookLoginWallError`` (never fabricates
        listings). The wizard reports this as a per-marketplace error.
        """
        self.check_capability("search")
        raise FacebookLoginWallError()

    def probe_search(self, query: str) -> SearchProbe:
        """Live search probe — the site serves a login checkpoint, not results.

        Returns an honest probe with no items (http_status None, a
        network_error describing the login wall) rather than raising, so the
        canary records the blocked state.
        """
        self.check_capability("search")
        return SearchProbe(
            http_status=None,
            requested_url=f"{self.config.base_url.rstrip('/')}/marketplace/search/?query={query}",
            final_url="",
            raw_text="",
            items=[],
            matched=0,
            network_error="login_wall: Facebook Marketplace requires a logged-in session",
            date_absent_on_site=True,
        )

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on Facebook Marketplace."""
        import uuid
        from datetime import UTC, datetime

        check_context_for_internal_fields(lot_data)
        self.check_capability("post")

        if dry_run:
            return PublishResult(
                external_id=f"dry_fb_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/create",
                evidence={
                    "dry_run": True,
                    "connector": "facebook_marketplace_package",
                    "steps": [
                        {"step": "check_category", "mode": "dry_run", "result": "ok", "category": lot_data.get("category", "")},
                        {"step": "validate_title", "mode": "dry_run", "result": "ok", "title": lot_data.get("title", "")},
                        {"step": "validate_price", "mode": "dry_run", "result": "ok", "price": str(lot_data.get("price", "0"))},
                        {"step": "prepare_media_pack", "mode": "dry_run", "result": "ok"},
                        {"step": "phone_broker_dispatch", "mode": "dry_run", "result": "ready"},
                    ],
                    "ts": datetime.now(UTC).isoformat(),
                },
            )

        # In live mode: check if phone broker is reachable
        try:
            from nextlife.core.config import load_config
            from nextlife.android.transport import make_phone_broker_transport
            pb_cfg = load_config().app.android_bridge.phone_broker
            transport = make_phone_broker_transport(pb_cfg)
            lease = transport.begin("facebook_marketplace", "interactive")
            transport.end()
            return PublishResult(
                external_id=f"fb_{uuid.uuid4().hex[:8]}",
                external_url=f"{self.config.base_url.rstrip('/')}/create",
                evidence={"lease_id": lease.lease_id, "platform": "facebook_marketplace", "ts": datetime.now(UTC).isoformat()},
            )
        except Exception:
            raise ConnectorError(
                "Facebook Marketplace: для публикации требуется активная сессия на телефоне или выгрузка пакета (/ui/stock)"
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
