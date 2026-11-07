"""Bazar.cz connector — HTML scraping via /hledat/<query>/."""

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from nextlife.connectors.base import BaseConnector, RawListing
from nextlife.core.config import MarketplaceConfig


class BazarConnector(BaseConnector):
    """Connector for Bazar.cz search and listing extraction."""

    def __init__(self, config: MarketplaceConfig, client: httpx.Client | None = None) -> None:
        super().__init__(config, client)
        self._base_url = "https://www.bazar.cz"

    def search(self, query: str) -> list[RawListing]:
        """Search listings on Bazar.cz by query string."""
        url = f"{self._base_url}/hledat/{quote(query)}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "cs,en;q=0.9",
        }
        try:
            resp = self.client.get(url, headers=headers, follow_redirects=True, timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text().lower()
        if "nenalezen žadný inzerát" in page_text or "vyhledáno 0 inzerátů" in page_text or "nenalezen žádný inzerát" in page_text:
            return []

        items: list[RawListing] = []
        for card in soup.select(".sale-item"):
            title_el = card.select_one("h2.title") or card.select_one("a[data-title]")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            link_el = card.select_one("a[href]")
            if not link_el:
                continue
            ext_url = urljoin(self._base_url, link_el["href"])

            # Parse price (e.g. "539 Kč" or "Cena dohodou")
            price_el = card.select_one(".price")
            price_text = price_el.get_text(strip=True) if price_el else ""
            price_digits = re.sub(r"[^\d]", "", price_text)
            price = Decimal(price_digits) if price_digits else Decimal("0")

            # Parse image
            img_el = card.select_one(".img-box img") or card.select_one("img")
            img_url = ""
            if img_el:
                img_url = img_el.get("src") or img_el.get("data-src") or ""
                if img_url.startswith("//"):
                    img_url = f"https:{img_url}"

            items.append(
                RawListing(
                    title=title,
                    price=price,
                    currency="CZK",
                    external_url=ext_url,
                    condition_text=None,
                    posted_at=datetime.now(timezone.utc),
                    raw={"image_url": img_url, "price_text": price_text},
                )
            )

        return items

    def post(self, draft: Any) -> Any:
        """Bazar.cz web posting requires user authentication / ASP.NET state."""
        return None

    def archive(self, listing_id: str) -> bool:
        return False
