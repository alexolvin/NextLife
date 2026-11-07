from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported, ConnectorError
from nextlife.connectors.bazos import BazosConnector
from nextlife.core.config import load_config


@pytest.fixture
def bazos_connector():
    config = load_config()
    return BazosConnector(config.marketplaces["bazos_cz"])


def test_bazos_build_search_url(bazos_connector):
    url = bazos_connector.build_search_url(
        query="RTX 3090",
        min_price=10000,
        max_price=20000,
        zip_code="11000",
    )
    assert "hledat=RTX+3090" in url
    assert "cenada=10000" in url
    assert "cenado=20000" in url
    assert "hlokalita=11000" in url


def test_bazos_publish_not_implemented_honest_error(bazos_connector):
    """Ч-5 (TZ M1): publication is not implemented (needs the owner's Bazoš
    account + phone confirmation). The connector must raise an honest error,
    not fabricate a PublishResult with a fake external_url."""
    lot_data = {
        "title": "NVIDIA RTX 3090 24GB",
        "description": "Grafická karta v perfektním stavu.",
        "category": "electronics",
        "item_code": "348",
    }
    with pytest.raises(ConnectorError, match="Bazoš live publish not in connector"):
        bazos_connector.publish(lot_data)


def test_bazos_publish_still_filters_internal_fields(bazos_connector):
    """The outgoing-field filter runs even though publish is not implemented."""
    lot_data = {
        "title": "NVIDIA RTX 3090",
        "description": "Popis",
        "price_floor": 10000,
    }
    with pytest.raises(InternalFieldLeakageError):
        bazos_connector.publish(lot_data)


def test_bazos_update_and_unpublish(bazos_connector, monkeypatch):
    """Edit is not implemented. Unpublish is supported via Android PhoneBroker."""
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        bazos_connector.update("bazos-348-123", lot_data)
    from nextlife.services import bazos_android_publish as bap

    monkeypatch.setattr(bap, "bazos_android_delete_listing", lambda **kw: {"ok": True})
    assert bazos_connector.unpublish("bazos-348-123") is True


def test_bazos_parses_sample_fixture(bazos_connector):
    """Fixture: live search probe 2026-10-04 (.a/reports/canaries/2026-10-04/
    bazos_cz_005939.html), 20 cards. D.2: refresh when > 30 days old."""
    fixture_path = Path(__file__).parent / "fixtures" / "bazos_sample.html"
    html = fixture_path.read_text(encoding="utf-8")
    listings = bazos_connector.search_with_html(html)
    assert len(listings) == 20
    assert listings[0].price == Decimal("22900")
    assert listings[0].currency == "CZK"
    # Ч-1: dates come from the card markup (span.velikost10), not invented.
    assert all(l.posted_at is not None for l in listings)
    assert listings[0].external_url.startswith("https://pc.bazos.cz/inzerat/")
