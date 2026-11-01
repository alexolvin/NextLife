"""Sbazar connector contract tests (M1 A2: repair to live 2026-10-03 markup).

Fixture: tests/fixtures/sbazar_sample.html — captured 2026-10-03 from
https://www.sbazar.cz/hledej/thinkpad (raw HTML: .a/reports/m1/probes/2026-10-03/sbazar_cz.html).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.connectors.sbazar import SbazarConnector
from nextlife.core.config import load_config


@pytest.fixture
def sbazar_connector():
    config = load_config()
    return SbazarConnector(config.marketplaces["sbazar_cz"])


def test_sbazar_build_search_url_uses_live_route(sbazar_connector):
    """Route must come from config and match the live /hledej/ route (old /hledat/ is 404)."""
    url = sbazar_connector.build_search_url(query="RTX 3090")
    assert "/hledej/RTX%203090" in url
    assert "/hledat/" not in url
    assert url.startswith("https://www.sbazar.cz")


def test_sbazar_search_404_returns_empty(sbazar_connector, monkeypatch):
    """Sbazar returns 404 for queries with no results; search() must return [] not raise."""
    import httpx

    class _FakeResp:
        status_code = 404
        text = ""

        def raise_for_status(self):
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", "http://x"), response=self)

    class _FakeClient:
        def get(self, url):
            return _FakeResp()

    monkeypatch.setattr(sbazar_connector, "client", _FakeClient())
    assert sbazar_connector.search("parovna") == []


def test_sbazar_build_search_url_price_params(sbazar_connector):
    url = sbazar_connector.build_search_url(
        query="iPhone 13",
        min_price=8000,
        max_price=12000,
    )
    assert "cena-od=8000" in url
    assert "cena-do=12000" in url


def test_sbazar_publish_not_implemented_honest_error(sbazar_connector):
    """Ч-5 (TZ M1): publication is not implemented (needs owner's Seznam
    session). The connector must raise an honest error, not fabricate a
    PublishResult with a fake external_url."""
    lot_data = {
        "title": "Apple iPhone 13 128GB Midnight",
        "description": "Telefon ve skvělém stavu, baterie 88 %.",
        "category": "electronics",
        "item_code": "138",
    }
    with pytest.raises(CapabilityNotSupported, match="post"):
        sbazar_connector.publish(lot_data)


def test_sbazar_publish_still_filters_internal_fields(sbazar_connector):
    """The outgoing-field filter runs even though publish is not implemented."""
    lot_data = {
        "title": "Apple iPhone 13",
        "description": "Telefon",
        "price_floor": 7000,
    }
    with pytest.raises(InternalFieldLeakageError):
        sbazar_connector.publish(lot_data)


def test_sbazar_update_and_unpublish_not_implemented(sbazar_connector):
    """Edit/archive are not implemented (same reason as publish)."""
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        sbazar_connector.update("sbazar-138-123", lot_data)
    with pytest.raises(CapabilityNotSupported, match="archive"):
        sbazar_connector.unpublish("sbazar-138-123")


def test_sbazar_parses_real_fixture(sbazar_connector):
    """3 cards in fixture; the Dohodou (non-numeric) price must be skipped."""
    fixture_path = Path(__file__).parent / "fixtures" / "sbazar_sample.html"
    html = fixture_path.read_text(encoding="utf-8")
    listings = sbazar_connector.search_with_html(html)
    assert len(listings) == 2
    assert listings[0].price == Decimal("10999")
    assert listings[0].currency == "CZK"
    assert listings[1].price == Decimal("3700")


def test_sbazar_title_is_listing_title_not_card_text(sbazar_connector):
    fixture_path = Path(__file__).parent / "fixtures" / "sbazar_sample.html"
    html = fixture_path.read_text(encoding="utf-8")
    listings = sbazar_connector.search_with_html(html)
    assert listings[0].title == "Lenovo ThinkPad P14s G.2 TouchLCD SSD 24GB RAM AMD"
    assert listings[1].title == "Notebook Lenovo ThinkPad"


def test_sbazar_external_url_absolute(sbazar_connector):
    fixture_path = Path(__file__).parent / "fixtures" / "sbazar_sample.html"
    html = fixture_path.read_text(encoding="utf-8")
    listings = sbazar_connector.search_with_html(html)
    assert listings[0].external_url == (
        "https://www.sbazar.cz/inzerat/233911845-lenovo-thinkpad-p14s-g2-touchlcd-ssd-24gb-ram-amd"
    )
    assert listings[1].external_url.startswith("https://www.sbazar.cz/inzerat/201095749-")
