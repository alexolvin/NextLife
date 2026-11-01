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
        """Publish lot on Annonce.

        Not implemented (TZ M1 A.4): requires the owner's Annonce account
        (web submission form). Until it is provided this must NOT return a
        fabricated PublishResult: Ч-5 — «опубликовано» only on the evidence
        of a real publication.
        """
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        raise ConnectorError(
            "Annonce publish not implemented: requires the owner's Annonce "
            "account (TZ M1 A.4). capabilities.post is false."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError(
            "Annonce update not implemented: requires the owner's Annonce "
            "account (TZ M1 A.4). capabilities.edit is false."
        )

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove listing — not implemented (see publish)."""
        self.check_capability("archive")
        raise ConnectorError(
            "Annonce unpublish not implemented: requires the owner's "
            "Annonce account (TZ M1 A.4). capabilities.archive is false."
        )
