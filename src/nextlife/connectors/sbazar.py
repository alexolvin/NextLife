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
        """Publish lot on Sbazar.

        Not implemented (TZ M1 A.2): real publication requires the owner's
        Seznam session (Playwright, saved browser profile — the owner logs in
        once). Until then this must NOT return a fabricated PublishResult:
        Ч-5 — «опубликовано» only on the evidence of a real publication.
        """
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        raise ConnectorError(
            "Sbazar publish not implemented: requires the owner's Seznam "
            "session (Playwright, TZ M1 A.4). capabilities.post is false."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing Sbazar listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError(
            "Sbazar update not implemented: requires the owner's Seznam "
            "session (Playwright, TZ M1 A.4). capabilities.edit is false."
        )

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove Sbazar listing — not implemented (see publish)."""
        self.check_capability("archive")
        raise ConnectorError(
            "Sbazar unpublish not implemented: requires the owner's Seznam "
            "session (Playwright, TZ M1 A.4). capabilities.archive is false."
        )
