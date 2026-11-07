"""Mimibazar.cz connector — search via /zmeny_dat.php JSON/HTML API."""

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from nextlife.connectors.base import BaseConnector, RawListing
from nextlife.core.config import MarketplaceConfig


class MimibazarConnector(BaseConnector):
    """Connector for Mimibazar.cz search and listing extraction."""

    def __init__(self, config: MarketplaceConfig, client: httpx.Client | None = None) -> None:
        super().__init__(config, client)
        self._base_url = "https://www.mimibazar.cz"

    def search(self, query: str) -> list[RawListing]:
        """Search listings on Mimibazar.cz via AJAX endpoint."""
        url = f"{self._base_url}/zmeny_dat.php"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        data = {
            "ajaxZmena": "10301",
            "sekce": "inzeraty",
            "hledej": query,
        }
        try:
            resp = self.client.post(url, data=data, headers=headers, follow_redirects=True, timeout=15)
            resp.raise_for_status()
            res_data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        # Parse items and HTML cards from JSON response
        html_cards = res_data.get("html", "")
        soup = BeautifulSoup(html_cards, "html.parser")
        card_map: dict[str, Any] = {}
        for card in soup.select(".card"):
            card_id = card.get("id", "").replace("card-", "")
            if card_id:
                card_map[card_id] = card

        items: list[RawListing] = []
        for it in res_data.get("items", []):
            item_id = str(it.get("id", ""))
            title = it.get("title", "")
            ext_url = it.get("url", "")
            if not title or not ext_url:
                continue

            card = card_map.get(item_id)
            price = Decimal("0")
            img_url = ""
            price_text = ""
            if card:
                price_el = card.select_one(".price__main") or card.select_one(".price")
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    price_digits = re.sub(r"[^\d]", "", price_text)
                    if price_digits:
                        price = Decimal(price_digits)

                img_el = card.select_one("img")
                if img_el:
                    img_url = img_el.get("data-src") or img_el.get("src") or ""
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
                    raw={"id": item_id, "image_url": img_url, "price_text": price_text},
                )
            )

        return items

    def post(self, draft: Any) -> Any:
        return None

    def archive(self, listing_id: str) -> bool:
        return False
