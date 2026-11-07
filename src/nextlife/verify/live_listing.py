"""Independent live-listing verifier (TZ Connectors, section 3).

This module does not import connectors and does not read their cookies,
storage state, or accounts. ``parse_listing_html`` is what the tests run
against saved HTML of real listings. ``LiveListingVerifier.verify`` opens
the public URL in a clean Playwright browser.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import imagehash
from bs4 import BeautifulSoup
from PIL import Image

from nextlife.verify.evidence import EvidenceRegistry

STATUSES = (
    "NOT_STARTED",
    "FAILED",
    "SUBMITTED_UNVERIFIED",
    "PENDING_MODERATION",
    "LIVE_VERIFIED",
    "PENDING_OWNER_CHECK",
    "ACCEPTED_BY_OWNER",
    "BLOCKED",
    "NOT_ALLOWED",
)

_MODERATION_MARKERS = (
    "čeká na schválení",
    "ceka na schvaleni",
    "na moderaci",
    "pending review",
    "wird geprüft",
    "in überprüfung",
    "oczekuje na moderację",
)


@dataclass
class ParsedListing:
    title: str = ""
    price_text: str = ""
    description: str = ""
    location: str = ""
    image_urls: list[str] = field(default_factory=list)
    body_text: str = ""


@dataclass
class PhotoMatch:
    live_url: str
    hamming_distance: int
    is_match: bool
    matched_original: str = ""


@dataclass
class LiveVerificationResult:
    status: str
    platform: str
    public_url: str
    external_id: str
    title_on_page: str = ""
    price_on_page: str = ""
    currency_on_page: str = ""
    location_on_page: str = ""
    photos_found: int = 0
    photos_matched: int = 0
    failure_reasons: list[str] = field(default_factory=list)
    evidence_dir: str = ""
    checked_at: str = ""


def normalize_whitespace(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _marker_fold(text: str) -> str:
    """Drop a single space between a number and a short unit (24 GB → 24GB)."""
    folded = normalize_whitespace(text).casefold()
    return re.sub(r"(\d)\s+([a-z]{1,4})\b", r"\1\2", folded)


def compute_phash(img_path: Path | str) -> imagehash.ImageHash:
    with Image.open(img_path) as img:
        return imagehash.phash(img.convert("RGB"))


def _digits(text: str) -> str:
    return re.sub(r"[^\d]", "", text or "")


def parse_listing_html(platform: str, html: str, page_url: str = "") -> ParsedListing:
    soup = BeautifulSoup(html or "", "html.parser")
    body_text = soup.get_text(" ", strip=True)
    parsed = ParsedListing(body_text=body_text)
    key = (platform or "").lower()

    if "bazos" in key:
        heading = soup.find("h1")
        parsed.title = heading.get_text(" ", strip=True) if heading else ""
        popis = soup.select_one(".popisdetail")
        parsed.description = popis.get_text(" ", strip=True) if popis else ""
        for row in soup.find_all("tr"):
            text = row.get_text(" ", strip=True)
            if text.startswith("Cena:"):
                parsed.price_text = text.split(":", 1)[1].strip()
            elif text.startswith("Lokalita:"):
                parsed.location = text.split(":", 1)[1].strip()
        for img in soup.select("img"):
            src = (
                img.get("data-flickity-lazyload")
                or img.get("data-src")
                or img.get("src")
                or ""
            )
            if src.startswith("//"):
                src = "https:" + src
            if page_url and src and not src.startswith("http"):
                src = urljoin(page_url, src)
            if re.search(r"/img/\d+/", src) and src not in parsed.image_urls:
                parsed.image_urls.append(src)
        return parsed

    heading = soup.find("h1")
    parsed.title = heading.get_text(" ", strip=True) if heading else ""
    parsed.description = body_text
    price_match = re.search(r"(\d[\d\s\.]{0,12})\s*(Kč|€|EUR|CZK)", body_text)
    if price_match:
        parsed.price_text = price_match.group(0)
    for img in soup.select("img"):
        src = img.get("src") or ""
        if src.startswith("http") and src not in parsed.image_urls:
            parsed.image_urls.append(src)
    return parsed


def decide_status(
    *,
    platform: str,
    public_url: str,
    external_id: str,
    parsed: ParsedListing,
    expected_title: str,
    expected_price: Decimal | int | str,
    expected_currency: str,
    expected_description: str,
    expected_markers: list[str],
    expected_location_fragments: list[str],
    photos_found: int,
    photos_matched: int,
    unmatched_photos: int,
    expected_photo_count: int,
    account_live_count: int | None,
    anonymous_blocked: bool = False,
    http_status: int = 200,
    artifacts_saved: bool = True,
) -> LiveVerificationResult:
    """Apply section 3.2. Does not soften a failed check into a pass."""
    result = LiveVerificationResult(
        status="FAILED",
        platform=platform,
        public_url=public_url,
        external_id=external_id,
        title_on_page=parsed.title,
        price_on_page=parsed.price_text,
        location_on_page=parsed.location,
        photos_found=photos_found,
        photos_matched=photos_matched,
        checked_at=datetime.now(UTC).isoformat(),
    )
    failures: list[str] = []

    if not public_url or not external_id:
        failures.append("нет публичного URL или внешнего ID")
    if anonymous_blocked or http_status in (403, 503):
        result.status = "PENDING_OWNER_CHECK"
        result.failure_reasons = [
            f"анонимный доступ заблокирован (HTTP {http_status})"
        ]
        return result

    folded_body = _marker_fold(parsed.body_text)
    if any(marker in folded_body for marker in _MODERATION_MARKERS):
        result.status = "PENDING_MODERATION"
        result.failure_reasons = ["площадка показывает объявление на модерации"]
        return result

    if normalize_whitespace(parsed.title).casefold() != normalize_whitespace(expected_title).casefold():
        failures.append(
            f"заголовок не совпал: на странице {parsed.title!r}, в карточке {expected_title!r}"
        )

    found_digits = _digits(parsed.price_text)
    expected_digits = _digits(str(expected_price))
    if found_digits != expected_digits:
        failures.append(
            f"цена не совпала: на странице {parsed.price_text!r}, ожидалась {expected_price}"
        )
    currency = (expected_currency or "").upper()
    price_up = (parsed.price_text or "").upper()
    if currency == "CZK" and "KČ" not in price_up and "CZK" not in price_up:
        failures.append(f"валюта на странице не CZK ({parsed.price_text!r})")
    if currency == "EUR" and "€" not in parsed.price_text and "EUR" not in price_up:
        failures.append(f"валюта на странице не EUR ({parsed.price_text!r})")
    result.currency_on_page = currency

    page_desc = _marker_fold(parsed.description)
    sent_desc = _marker_fold(expected_description)
    if sent_desc and page_desc != sent_desc:
        failures.append("описание на странице не совпало с отправленным после нормализации пробелов")
    for marker in expected_markers:
        if _marker_fold(marker) not in page_desc and _marker_fold(marker) not in folded_body:
            failures.append(f"в описании нет маркера {marker!r}")

    loc_fold = normalize_whitespace(parsed.location).casefold()
    for fragment in expected_location_fragments:
        if fragment.casefold() not in loc_fold:
            failures.append(f"в локации нет {fragment!r} (на странице {parsed.location!r})")

    if photos_found != expected_photo_count:
        failures.append(
            f"число фото {photos_found} не равно ожидаемому {expected_photo_count}"
        )
    if unmatched_photos:
        failures.append(f"фото без пары по pHash: {unmatched_photos}")
    if photos_found and photos_matched != photos_found:
        failures.append(f"совпало фото {photos_matched} из {photos_found}")

    if account_live_count is None:
        failures.append("не проверена единственность объявления в аккаунте")
    elif account_live_count != 1:
        failures.append(f"живых объявлений лота в аккаунте: {account_live_count}, нужно ровно 1")

    if not artifacts_saved:
        failures.append("не сохранены HTML и скриншот страницы")

    result.failure_reasons = failures
    if not failures:
        result.status = "LIVE_VERIFIED"
    return result


class LiveListingVerifier:
    def __init__(self, headless: bool = True, timeout_ms: int = 30000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms

    def verify(
        self,
        *,
        platform: str,
        public_url: str,
        external_id: str,
        expected_title: str,
        expected_price: Decimal | int | str,
        expected_currency: str,
        expected_description: str,
        expected_markers: list[str],
        expected_location_fragments: list[str],
        original_photo_paths: list[str | Path],
        evidence_root: Path,
        run_id: str,
        platform_photo_limit: int | None = None,
        account_live_count: int | None = None,
    ) -> LiveVerificationResult:
        registry = EvidenceRegistry(evidence_root)
        directory = registry.run_dir(platform, run_id)
        screenshot = directory / "page.png"
        html_path = directory / "page.html"
        photos_dir = directory / "photos"
        photos_dir.mkdir(exist_ok=True)

        anonymous_blocked = False
        http_status = 0
        html = ""
        try:
            from playwright.sync_api import sync_playwright
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:
            result = decide_status(
                platform=platform,
                public_url=public_url,
                external_id=external_id,
                parsed=ParsedListing(),
                expected_title=expected_title,
                expected_price=expected_price,
                expected_currency=expected_currency,
                expected_description=expected_description,
                expected_markers=expected_markers,
                expected_location_fragments=expected_location_fragments,
                photos_found=0,
                photos_matched=0,
                unmatched_photos=0,
                expected_photo_count=0,
                account_live_count=account_live_count,
                anonymous_blocked=True,
                http_status=0,
                artifacts_saved=False,
            )
            result.failure_reasons = [f"Playwright недоступен: {exc}"]
            result.status = "FAILED"
            result.evidence_dir = str(directory)
            return result

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 1024},
                storage_state=None,
            )
            page = context.new_page()
            try:
                response = page.goto(public_url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                http_status = response.status if response else 0
                html = page.content()
                if http_status in (403, 503) or "cf-turnstile" in html or "challenge-platform" in html or "Just a moment" in html:
                    anonymous_blocked = True
                page.screenshot(path=str(screenshot), full_page=True)
            except PlaywrightTimeoutError:
                html = page.content()
                http_status = http_status or 0
            finally:
                browser.close()

        if html and not html_path.exists():
            html_path.write_text(html, encoding="utf-8")
        parsed = parse_listing_html(platform, html, public_url)
        originals = [Path(p) for p in original_photo_paths if Path(p).is_file()]
        limit = platform_photo_limit if platform_photo_limit else len(originals)
        expected_count = min(len(originals), limit) if originals else 0
        matched, unmatched = self._match_photos(parsed.image_urls, originals, photos_dir)
        result = decide_status(
            platform=platform,
            public_url=public_url,
            external_id=external_id,
            parsed=parsed,
            expected_title=expected_title,
            expected_price=expected_price,
            expected_currency=expected_currency,
            expected_description=expected_description,
            expected_markers=expected_markers,
            expected_location_fragments=expected_location_fragments,
            photos_found=len(parsed.image_urls),
            photos_matched=matched,
            unmatched_photos=unmatched,
            expected_photo_count=expected_count,
            account_live_count=account_live_count,
            anonymous_blocked=anonymous_blocked,
            http_status=http_status,
            artifacts_saved=screenshot.is_file() and html_path.is_file(),
        )
        result.evidence_dir = str(directory)
        payload = asdict(result)
        if not (directory / "result.json").exists():
            registry.write_json(platform, run_id, "result.json", payload)
        registry.finalize(platform, run_id)
        return result

    def _match_photos(
        self,
        urls: list[str],
        originals: list[Path],
        photos_dir: Path,
    ) -> tuple[int, int]:
        if not urls:
            return 0, 0
        orig_hashes = [(str(path), compute_phash(path)) for path in originals]
        matched = 0
        unmatched = 0
        with httpx.Client(timeout=20) as client:
            for index, url in enumerate(urls):
                try:
                    response = client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    unmatched += 1
                    continue
                dest = photos_dir / f"live_{index:02d}.jpg"
                if not dest.exists():
                    dest.write_bytes(response.content)
                live_hash = compute_phash(dest)
                best = min((live_hash - orig for _, orig in orig_hashes), default=999)
                if best <= 10:
                    matched += 1
                else:
                    unmatched += 1
        return matched, unmatched


def result_to_json(result: LiveVerificationResult) -> dict[str, Any]:
    return asdict(result)
