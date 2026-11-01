"""Willhaben.at connector tests (TZ-connectors-add-4).

Live evidence 2026-10-22: .a/reports/probe-willhaben_mp_kw.html (fixture:
tests/fixtures/willhaben_nextdata.json — the __NEXT_DATA__ object, 30 ads;
fields in attributes.attribute[]: HEADING, PRICE/AMOUNT (dot decimal),
SEO_URL, LOCATION, PUBLISHED_String (ISO 8601), ISPRIVATE).
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.connectors.willhaben import (
    WillhabenConnector,
    _parse_adverts,
    extract_next_data,
)
from nextlife.core.config import load_config


@pytest.fixture
def wh_connector():
    config = load_config()
    return WillhabenConnector(config.marketplaces["willhaben_at"])


def _nextdata_fixture() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "willhaben_nextdata.json").read_text(encoding="utf-8")
    )


def test_wh_build_search_url(wh_connector):
    url = wh_connector.build_search_url(query="notebook")
    assert url == "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/?keyword=notebook"


def test_wh_parses_nextdata_fixture(wh_connector):
    """Fixture: live probe 2026-10-22, 30 ads, all priced. German dot-decimal
    price, ISO date, SEO_URL -> web URL, ISPRIVATE -> seller_type."""
    state = _nextdata_fixture()
    listings = _parse_adverts(state, wh_connector.config.base_url, wh_connector.config.currency)
    assert len(listings) == 30
    first = listings[0]
    assert first.title == 'Dell Latitude 5520 - i5-1135G7 / 16GB / 256GB SSD / 15,6" FHD / Win 11 Pro'
    assert first.price == Decimal("369.0")
    assert first.currency == "EUR"
    assert first.external_url.startswith("https://www.willhaben.at/iad/kaufen-und-verkaufen/d/")
    assert first.external_url.endswith("2084887372/")
    assert first.posted_at is not None
    assert first.posted_at.tzinfo is not None
    assert first.raw["seller_type"] == "business"  # ISPRIVATE=0
    assert all(l.price >= 0 for l in listings)


def test_extract_next_data_from_html(wh_connector):
    """The state is in a <script id="__NEXT_DATA__">; extraction must parse it.
    Absent marker -> empty dict (honest structure change)."""
    state = _nextdata_fixture()
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}</script>'
    assert extract_next_data(html) == state
    assert extract_next_data("<html><body>no marker</body></html>") == {}


def test_wh_publish_not_implemented_honest_error(wh_connector):
    """Posting is a TZ-connectors-add-4 non-goal (SSL-pinned mobile API is not
    used); the connector raises an honest error (Ч-5)."""
    lot_data = {"title": "HP Omen 15", "description": "Notebook.", "item_code": "702"}
    with pytest.raises(CapabilityNotSupported, match="post"):
        wh_connector.publish(lot_data)


def test_wh_publish_still_filters_internal_fields(wh_connector):
    lot_data = {"title": "HP Omen", "description": "Popis", "price_floor": 15000}
    with pytest.raises(InternalFieldLeakageError):
        wh_connector.publish(lot_data)


def test_wh_update_and_unpublish_not_implemented(wh_connector):
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        wh_connector.update("wh-123", lot_data)
    with pytest.raises(CapabilityNotSupported, match="archive"):
        wh_connector.unpublish("wh-123")
