"""Allegro.cz connector — Android / API bridge placeholder."""

from typing import Any

import httpx

from nextlife.connectors.base import BaseConnector, RawListing
from nextlife.core.config import MarketplaceConfig


class AllegroConnector(BaseConnector):
    """Connector for Allegro.cz.

    Public web search is protected by Cloudflare / Datadome bot wall (403 Forbidden).
    Official REST API requires Allegro Developer OAuth app credentials (available
    primarily for business accounts).
    Android access mode uses mobile app (pl.allegro) on physical device.
    """

    def __init__(self, config: MarketplaceConfig, client: httpx.Client | None = None) -> None:
        super().__init__(config, client)
        self._base_url = "https://allegro.cz"

    def search(self, query: str) -> list[RawListing]:
        """Public web search is blocked by bot challenge; returns empty list safely."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        }
        try:
            resp = self.client.get(f"{self._base_url}/listing?string={query}", headers=headers, timeout=10)
            if resp.status_code == 403 or "challenge" in resp.text.lower():
                return []
        except httpx.HTTPError:
            return []
        return []

    def post(self, draft: Any) -> Any:
        return None

    def archive(self, listing_id: str) -> bool:
        return False
