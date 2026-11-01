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
        """Publish lot on Hyperinzerce.

        Not implemented (TZ M1 A.4): requires the owner's Hyperinzerce
        account (web submission form). Until it is provided this must NOT
        return a fabricated PublishResult: Ч-5 — «опубликовано» only on the
        evidence of a real publication.
        """
        check_context_for_internal_fields(lot_data)
        self.check_capability("post")
        raise ConnectorError(
            "Hyperinzerce publish not implemented: requires the owner's "
            "Hyperinzerce account (TZ M1 A.4). capabilities.post is false."
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing listing — not implemented (see publish)."""
        check_context_for_internal_fields(lot_data)
        self.check_capability("edit")
        raise ConnectorError(
            "Hyperinzerce update not implemented: requires the owner's "
            "Hyperinzerce account (TZ M1 A.4). capabilities.edit is false."
        )

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove listing — not implemented (see publish)."""
        self.check_capability("archive")
        raise ConnectorError(
            "Hyperinzerce unpublish not implemented: requires the owner's "
            "Hyperinzerce account (TZ M1 A.4). capabilities.archive is false."
        )
