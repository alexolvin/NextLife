"""eBay connector — search and listing contracts."""

import re
import urllib.parse
from decimal import Decimal
from typing import Any

import httpx
from bs4 import BeautifulSoup

from nextlife.connectors.base import BaseConnector, RawListing
from nextlife.core.config import MarketplaceConfig

_EBAY_PRICE_RE = re.compile(r"([0-9]+(?:[.,][0-9]{2})?)")


def _parse_ebay_price(text: str) -> tuple[Decimal, str]:
    """Parse price and currency from eBay price string like '$199.99', 'EUR 149.00', '149,00 €'."""
    curr = "EUR"
    lower = text.lower()
    if "$" in text or "usd" in lower:
        curr = "USD"
    elif "£" in text or "gbp" in lower:
        curr = "GBP"
    elif "kč" in lower or "czk" in lower:
        curr = "CZK"
    elif "eur" in lower or "€" in text:
        curr = "EUR"

    cleaned = text.replace("\xa0", " ").strip()
    m = _EBAY_PRICE_RE.search(cleaned.replace(" ", ""))
    if not m:
        return Decimal("0"), curr
    num_str = m.group(1).replace(",", ".")
    try:
        val = Decimal(num_str)
        return val, curr
    except Exception:
        return Decimal("0"), curr


class EbayConnector(BaseConnector):
    """Connector for eBay (search and listing contracts)."""

    def __init__(self, config: MarketplaceConfig, client: httpx.Client | None = None) -> None:
        super().__init__(config, client)
        self._base_url = getattr(config, "base_url", "https://www.ebay.com").rstrip("/")

    def parse_cards(self, html: str) -> list[RawListing]:
        """Parse eBay search result HTML into canonical RawListing items."""
        soup = BeautifulSoup(html, "html.parser")
        items: list[RawListing] = []

        for card in soup.select(".s-item, .s-card"):
            title_el = card.select_one(".s-item__title") or card.select_one(".s-card__title")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title or title.lower() in ("shop on ebay", "new listing"):
                continue

            link_el = card.select_one(".s-item__link") or card.select_one("a[href]")
            link = link_el.get("href") if link_el else ""
            if not link or "javascript:" in link:
                continue

            price_el = card.select_one(".s-item__price") or card.select_one(".s-card__price")
            price_text = price_el.get_text(strip=True) if price_el else ""
            price, currency = _parse_ebay_price(price_text)

            img_el = card.select_one(".s-item__image-img, img[src]")
            img_url = (img_el.get("src") or img_el.get("data-src") or "") if img_el else ""

            items.append(
                RawListing(
                    title=title,
                    price=price,
                    currency=currency,
                    external_url=link,
                    condition_text=None,
                    posted_at=None,
                    raw={"image_url": img_url, "price_text": price_text},
                )
            )
        return items

    def search(self, query: str) -> list[RawListing]:
        """Search listings on eBay."""
        url = f"{self._base_url}/sch/i.html?_nkw={urllib.parse.quote(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            resp = self.client.get(url, headers=headers, follow_redirects=True, timeout=12)
            if resp.status_code == 200:
                return self.parse_cards(resp.text)
            if resp.status_code == 403 or "challenge" in resp.text.lower() or "captcha" in resp.text.lower():
                return []
        except httpx.HTTPError:
            return []
        return []

    def post(self, draft: Any) -> Any:
        return None

    def archive(self, listing_id: str) -> bool:
        return False
