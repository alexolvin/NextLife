"""OLX.pl connector tests (TZ-connectors-add-4).

Live evidence 2026-10-22: .a/reports/probe-olx_pl-cffi.html (fixture:
tests/fixtures/olx_prerendered.json — the __PRERENDERED_STATE__ object, 51
ads; all carry createdTime; price.regularPrice.value; photos[]; location
pathName; UTF-8 Polish titles must survive the round-trip).
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.connectors.olx import OlxConnector, _parse_ads, extract_prerendered_state
from nextlife.core.config import load_config


@pytest.fixture
def olx_connector():
    config = load_config()
    return OlxConnector(config.marketplaces["olx_pl"])


def _state_fixture() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "olx_prerendered.json").read_text(encoding="utf-8")
    )


def test_olx_build_search_url(olx_connector):
    url = olx_connector.build_search_url(query="rtx 3090")
    assert url == "https://www.olx.pl/oferty/q-rtx%203090/"


def test_olx_parses_prerendered_fixture(olx_connector):
    """Fixture: live probe 2026-10-22, 51 ads, all priced. Polish UTF-8
    titles must not be mojibake; createdTime parses to a tz-aware datetime."""
    state = _state_fixture()
    listings = _parse_ads(state, olx_connector.config.base_url, olx_connector.config.currency)
    assert len(listings) == 51
    first = listings[0]
    assert first.title == "Windows 11 Świetny notebook do Internetu szkoły biura na studia usbc hdmi wifi kamera SSD pc nvme m.2 Okazja! tani laptop"
    assert "Świetny" in first.title  # UTF-8 intact, not «Åwietny»
    assert first.price == Decimal("1120")
    assert first.currency == "PLN"
    assert first.external_url.startswith("https://www.olx.pl/d/oferta/")
    assert first.posted_at is not None
    assert first.posted_at.tzinfo is not None
    assert all(l.price >= 0 for l in listings)


def test_extract_prerendered_state_from_html(olx_connector):
    """The state is a JSON-escaped string in a <script>; extraction must
    double-decode it. Absent marker -> empty dict (honest structure change)."""
    state = _state_fixture()
    escaped = json.dumps(json.dumps(state, ensure_ascii=False))  # the inner string, re-escaped
    html = f'<script>window.__PRERENDERED_STATE__={escaped};</script>'
    parsed = extract_prerendered_state(html)
    assert parsed == state
    assert extract_prerendered_state("<html><body>no marker</body></html>") == {}


def test_olx_publish_not_implemented_honest_error(olx_connector):
    """Posting is a TZ-connectors-add-4 non-goal (partner API is posting-only
    and is not used); the connector raises an honest error (Ч-5)."""
    lot_data = {"title": "HP Omen 15", "description": "Notebook.", "item_code": "702"}
    with pytest.raises(CapabilityNotSupported, match="post"):
        olx_connector.publish(lot_data)


def test_olx_publish_still_filters_internal_fields(olx_connector):
    lot_data = {"title": "HP Omen", "description": "Popis", "price_floor": 15000}
    with pytest.raises(InternalFieldLeakageError):
        olx_connector.publish(lot_data)


def test_olx_update_and_unpublish_not_implemented(olx_connector):
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        olx_connector.update("olx-123", lot_data)
    with pytest.raises(CapabilityNotSupported, match="archive"):
        olx_connector.unpublish("olx-123")
