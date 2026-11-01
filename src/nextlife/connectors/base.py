"""Base connector abstractions and data contracts v2 (TZ v2.0 Section III.9, VI.1, v1.1 Section 6.2)."""

import re
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from bs4 import BeautifulSoup

from nextlife.comms.outgoing_filter import (
    InternalFieldLeakageError,
    check_context_for_internal_fields,
)
from nextlife.core.config import MarketplaceConfig
from nextlife.core.exceptions import NextLifeError


class ConnectorError(NextLifeError):
    """Base error for connector operations."""


class ConnectorConfigError(ConnectorError):
    """Raised when marketplace configuration or selector is missing/invalid."""


class CapabilityNotSupported(ConnectorError):
    """Raised when an operation is not supported by marketplace capabilities."""


class UnsafeUrlNavigationError(ConnectorError):
    """Raised when automated agent/connector attempts to navigate an untrusted external URL."""


@dataclass(frozen=True)
class RawListing:
    title: str
    price: Decimal
    currency: str
    external_url: str
    condition_text: str | None
    posted_at: datetime | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PublishResult:
    external_id: str
    external_url: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class SearchProbe:
    """Result of one live search probe (TZ M1 D.1/A.3): transport-agnostic.

    ``matched`` is the number of result records the raw response contains
    (HTML cards / JSON items) — the canary uses it to distinguish «records
    present but unparsable» (schema drift) from «truly empty result».
    """

    http_status: int | None
    requested_url: str
    final_url: str
    raw_text: str
    items: list[RawListing]
    matched: int
    network_error: str | None
    date_absent_on_site: bool


class BaseConnector(ABC):
    """Base connector implementing common parsing, capability checks, and HTTP mechanics."""

    def __init__(self, config: MarketplaceConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self.key = config.key
        default_headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        self.client = client or httpx.Client(headers=default_headers, follow_redirects=True, timeout=20.0)
        self._validate_selectors()

    def _validate_selectors(self) -> None:
        if self.config.access_mode == "DIRECT" and self.config.search_mode == "html":
            required_selectors = ("search_result", "price")
            for sel in required_selectors:
                if sel not in self.config.selectors or not self.config.selectors[sel]:
                    raise ConnectorConfigError(
                        f"Missing required selector '{sel}' in marketplace '{self.key}' configuration."
                    )

    def check_capability(self, capability_name: str) -> None:
        """Verify that the marketplace explicitly supports the requested action."""
        caps = getattr(self.config, "capabilities", {})
        if isinstance(caps, dict):
            supported = caps.get(capability_name, False)
        else:
            supported = getattr(caps, capability_name, False)

        if not supported:
            raise CapabilityNotSupported(
                f"Marketplace '{self.key}' does not support '{capability_name}' capability."
            )

    @staticmethod
    def assert_safe_url(url: str) -> None:
        """Security guard: prohibit opening links from buyer messages (TZ v2.0 Section III.9)."""
        if not url:
            return
        url_lower = url.lower().strip()
        # Prohibit opening external payment or third-party redirection links
        forbidden_patterns = [
            r"t\.me/",
            r"wa\.me/",
            r"pay",
            r"platba",
            r"post-online",
            r"doprava-overeni",
            r"zasilkovna-platba",
            r"dpd-overeni",
        ]
        for pat in forbidden_patterns:
            if re.search(pat, url_lower):
                raise UnsafeUrlNavigationError(
                    f"Automated navigation to untrusted URL pattern '{pat}' is strictly blocked for security."
                )

    def open_in_browser(self, url: str, *, source: str = "CONNECTOR") -> dict[str, Any]:
        """Navigate to URL via browser automation layer with message URL protection."""
        from nextlife.connectors.browser import browser_automation
        return browser_automation.navigate(url, source=source)

    def extract_price_digits(self, text: str) -> Decimal:
        """Extract numeric price from string, stripping whitespace and currency symbols."""
        clean = re.sub(r"[^\d.,]", "", text)
        if not clean:
            raise ConnectorError(f"Could not extract numeric price from text: '{text}'")
        if "," in clean and "." in clean:
            clean = clean.replace(".", "").replace(",", ".")
        elif "," in clean:
            clean = clean.replace(",", ".")
        return Decimal(clean)

    @staticmethod
    def parse_listing_date(text: str | None) -> datetime | None:
        """Parse a listing date from card text (Ч-1: never fabricated).

        Supported: 'DD.MM. YYYY' (e.g. Bazoš '- [4.10. 2026]'),
        'dnes HH:MM', 'včera HH:MM', ISO 8601. Returns None when no date
        is present or the text is not parseable.
        """
        if not text:
            return None
        # Bazoš writes the date with a space before the year: '[4.10. 2026]';
        # Annonce puts a space after every dot: '29. 9. 2026' (live 2026-10-04).
        m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b", text)
        if m:
            try:
                return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=UTC)
            except ValueError:
                return None
        today = datetime.now(UTC).date()
        m = re.search(r"\bdnes\s+(\d{1,2}):(\d{2})\b", text)
        if m:
            try:
                return datetime(today.year, today.month, today.day, int(m.group(1)), int(m.group(2)), tzinfo=UTC)
            except ValueError:
                return None
        m = re.search(r"\bvčera\s+(\d{1,2}):(\d{2})\b", text)
        if m:
            try:
                yesterday = today - timedelta(days=1)
                return datetime(yesterday.year, yesterday.month, yesterday.day, int(m.group(1)), int(m.group(2)), tzinfo=UTC)
            except ValueError:
                return None
        m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", text)
        if m:
            try:
                return datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
            except ValueError:
                return None
        return None

    def parse_html_listings(self, html: str) -> list[RawListing]:
        """Parse raw HTML into normalized RawListing items using config selectors."""
        soup = BeautifulSoup(html, "html.parser")
        result_selector = self.config.selectors["search_result"]
        price_selector = self.config.selectors["price"]

        items: list[RawListing] = []
        for card in soup.select(result_selector):
            price_elem = card.select_one(price_selector)
            if not price_elem:
                continue

            try:
                price_val = self.extract_price_digits(price_elem.get_text())
            except Exception:
                continue

            link_candidates = [a for a in card.select("a[href]") if a.get_text().strip()]
            link_elem = link_candidates[0] if link_candidates else card.select_one("a[href]")
            # Optional dedicated title selector (config selectors.title); without it
            # the title falls back to the link text as before.
            title_selector = self.config.selectors.get("title")
            title = ""
            if title_selector:
                title_elem = card.select_one(title_selector)
                if title_elem:
                    title = title_elem.get_text(" ", strip=True)
            if not title:
                title = (
                    link_elem.get_text().strip()
                    if link_elem and link_elem.get_text().strip()
                    else card.get_text().strip().split("\n")[0]
                )
            href_raw = link_elem["href"] if link_elem and "href" in link_elem.attrs else ""
            href = str(href_raw[0]) if isinstance(href_raw, list) else str(href_raw)
            if href and not href.startswith("http"):
                href = self.config.base_url.rstrip("/") + "/" + href.lstrip("/")

            # Honest date (Ч-1): only a date present in the card markup.
            # Without config selectors.date the site does not expose a date
            # on search cards and posted_at must stay None.
            posted_at: datetime | None = None
            date_selector = self.config.selectors.get("date")
            if date_selector:
                for date_elem in card.select(date_selector):
                    posted_at = self.parse_listing_date(date_elem.get_text(" ", strip=True))
                    if posted_at is not None:
                        break

            image_url = self._extract_card_image(card)
            items.append(
                RawListing(
                    title=title,
                    price=price_val,
                    currency=self.config.currency,
                    external_url=href,
                    condition_text=None,
                    posted_at=posted_at,
                    raw={"html_snippet": str(card)[:500], "image_url": image_url},
                )
            )
        return items

    def _extract_card_image(self, card: Any) -> str:
        """Best-effort thumbnail URL from a search-result card.

        Looks for the first ``img`` in the card (``src`` or lazy-load
        ``data-src``) and resolves relative URLs against the marketplace
        base URL. When the card has no usable ``src`` (Sbazar stores the
        product photo only in ``srcset``), falls back to parsing ``srcset``.
        Returns "" when the card has no image (never fabricated).
        """
        img = card.select_one("img")
        if img is None:
            return ""
        src = img.get("src") or img.get("data-src") or ""
        if src and not src.startswith("data:"):
            if not src.startswith("http"):
                src = self.config.base_url.rstrip("/") + "/" + src.lstrip("/")
            return src
        srcset = img.get("srcset")
        if srcset:
            return self._pick_srcset_url(srcset)
        return ""

    def _pick_srcset_url(self, srcset: str) -> str:
        """Pick the best product-image URL from a ``srcset`` value.

        Prefers a width descriptor closest to 400px within 300–600px,
        otherwise the largest available. Skips ``data:`` URLs and resolves
        protocol-relative (``//host``) / relative URLs.

        Sbazar's srcset URLs contain commas inside the query string
        (``?fl=exf|res,606,606,3|webp,75``), so the value is tokenized on
        whitespace (URL + optional ``<int>w`` descriptor per entry) rather
        than naively split on commas.
        """
        candidates: list[tuple[int, str]] = []
        tokens = srcset.split()
        i = 0
        while i < len(tokens):
            url = tokens[i].strip(",")
            i += 1
            if not url or url.startswith("data:"):
                continue
            width = 0
            if i < len(tokens):
                desc = tokens[i].strip(",")
                m = re.fullmatch(r"(\d+)w", desc)
                if m:
                    width = int(m.group(1))
                    i += 1
            candidates.append((width, url))
        if not candidates:
            return ""
        in_range = [c for c in candidates if 300 <= c[0] <= 600]
        if in_range:
            _, url = min(in_range, key=lambda c: abs(c[0] - 400))
        else:
            _, url = max(candidates, key=lambda c: c[0])
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith("http"):
            url = self.config.base_url.rstrip("/") + "/" + url.lstrip("/")
        return url

    def search_with_html(self, html_fixture: str) -> list[RawListing]:
        """Parse listings from recorded fixture HTML."""
        return self.parse_html_listings(html_fixture)

    def search_with_json(self, json_fixture: str) -> list[RawListing]:
        """Parse listings from a recorded JSON API response (fixture).

        Supported by JSON-mode connectors (``search_mode: json``, e.g. Aukro,
        TZ M1 A.3); the default raises — HTML-mode sites have no JSON fixture.
        """
        raise NotImplementedError(
            f"Connector '{self.key}': search_with_json not supported (search_mode is not json)."
        )

    def build_search_url(
        self,
        query: str,
        min_price: int | None = None,
        max_price: int | None = None,
        zip_code: str | None = None,
    ) -> str:
        """Build the canonical search URL from the config route map (TZ M1 A.4).

        The route template and the marketplace's parameter names live in
        ``config/marketplaces/<key>.yaml`` (``routes``), not in connector code.
        """
        routes = self.config.routes or {}
        route = routes.get("search")
        if not route:
            raise ConnectorConfigError(
                f"Missing 'routes.search' in marketplace '{self.key}' configuration."
            )
        param_names: dict[str, str] = routes.get("search_params") or {}
        params: dict[str, Any] = {}
        if param_names.get("query"):
            params[param_names["query"]] = query.strip()
        if min_price is not None and param_names.get("min_price"):
            params[param_names["min_price"]] = min_price
        if max_price is not None and param_names.get("max_price"):
            params[param_names["max_price"]] = max_price
        if zip_code and param_names.get("zip_code"):
            params[param_names["zip_code"]] = zip_code
        path = route.format(query=urllib.parse.quote(query.strip()))
        url = f"{self.config.base_url.rstrip('/')}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return url

    def probe_search(self, query: str) -> SearchProbe:
        """One live search probe (TZ M1 D.1). Default: GET + HTML cards.

        JSON-mode connectors (``search_mode: json``, e.g. Aukro) override
        this with their site API call.
        """
        self.check_capability("search")
        url = self.build_search_url(query)
        final_url = ""
        text = ""
        status: int | None = None
        network_error: str | None = None
        try:
            resp = self.client.get(url)
            final_url = str(resp.url)
            status = resp.status_code
            text = resp.text
        except httpx.HTTPError as exc:
            network_error = f"{type(exc).__name__}: {exc}"

        items: list[RawListing] = []
        matched = 0
        if status == 200:
            soup = BeautifulSoup(text, "html.parser")
            matched = len(soup.select(self.config.selectors["search_result"]))
            if matched:
                items = self.parse_html_listings(text)
        return SearchProbe(
            http_status=status,
            requested_url=url,
            final_url=final_url,
            raw_text=text,
            items=items,
            matched=matched,
            network_error=network_error,
            date_absent_on_site=not bool(self.config.selectors.get("date")),
        )

    @abstractmethod
    def search(self, query: str) -> list[RawListing]:
        """Execute search on the marketplace."""
        raise NotImplementedError

    def publish(self, lot_data: dict[str, Any], dry_run: bool = False) -> PublishResult:
        """Publish lot on marketplace with strict outgoing internal field filter."""
        self.check_capability("post")
        check_context_for_internal_fields(lot_data)
        # Default placeholder / abstract implementation
        return PublishResult(
            external_id=f"{self.key}-mock-12345",
            external_url=f"{self.config.base_url}/inzerat/{self.key}-mock-12345",
            evidence={"dry_run": dry_run, "published_at": datetime.now(UTC).isoformat()},
        )

    def update(self, external_id: str, lot_data: dict[str, Any]) -> PublishResult:
        """Update existing listing with strict outgoing internal field filter."""
        self.check_capability("edit")
        check_context_for_internal_fields(lot_data)
        return PublishResult(
            external_id=external_id,
            external_url=f"{self.config.base_url}/inzerat/{external_id}",
            evidence={"updated_at": datetime.now(UTC).isoformat()},
        )

    def unpublish(self, external_id: str) -> bool:
        """Archive/remove listing from marketplace."""
        self.check_capability("archive")
        return True

    def fetch_messages(self, thread_id: str | None = None) -> list[dict[str, Any]]:
        """Fetch incoming messages for marketplace thread."""
        self.check_capability("messaging")
        return []

    def send_message(self, thread_id: str, text: str) -> bool:
        """Send message with strict outgoing internal field filter."""
        self.check_capability("messaging")
        from nextlife.comms.outgoing_filter import FORBIDDEN_INTERNAL_FIELDS
        for forbidden in FORBIDDEN_INTERNAL_FIELDS:
            if forbidden in text:
                raise InternalFieldLeakageError(
                    f"Forbidden keyword '{forbidden}' leaked into outgoing message text."
                )
        return True
