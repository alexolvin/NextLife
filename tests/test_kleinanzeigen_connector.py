"""Kleinanzeigen.de connector tests (TZ-connectors-add-4).

Live evidence 2026-10-22: .a/reports/probe-kleinanzeigen_de-httpx.html
(fixture: tests/fixtures/kleinanzeigen_sample.html — 27 <article data-adid>
cards; 25 with a numeric price, 2 bare «VB» skipped; German price format
«1.000 € VB»; no date on search cards -> posted_at None).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.connectors.kleinanzeigen import KleinanzeigenConnector, _parse_german_price
from nextlife.core.config import load_config


@pytest.fixture
def kz_connector():
    config = load_config()
    return KleinanzeigenConnector(config.marketplaces["kleinanzeigen_de"])


def test_kz_build_search_url(kz_connector):
    url = kz_connector.build_search_url(query="rtx 3090")
    assert url == "https://www.kleinanzeigen.de/s-rtx%203090/k0"


def test_kz_parses_sample_fixture(kz_connector):
    """Fixture: live probe 2026-10-22, 27 cards, 25 priced (2 bare «VB»
    skipped). German price «1.000 € VB» -> 1000. No date -> posted_at None."""
    fixture_path = Path(__file__).parent / "fixtures" / "kleinanzeigen_sample.html"
    listings = kz_connector.search_with_html(fixture_path.read_text(encoding="utf-8"))
    assert len(listings) == 25
    first = listings[0]
    # The first card («Sofort Barzahlung …», bare «VB», no numeric price) is
    # skipped; the first priced card is «PC-Hilfe vor Ort …».
    assert first.title == "PC-Hilfe vor Ort | Notebook, WLAN, Drucker & IT-Service"
    assert first.price == Decimal("67")
    assert first.currency == "EUR"
    assert first.external_url.startswith("https://www.kleinanzeigen.de/s-anzeige/")
    assert first.posted_at is None
    assert all(l.price > 0 for l in listings)
    # German thousands separator: «1.000 € VB» parses to 1000, not 1.0
    prices = {l.price for l in listings}
    assert Decimal("1000") in prices


def test_parse_german_price():
    assert _parse_german_price("67 € VB") == Decimal("67")
    assert _parse_german_price("1.000 € VB") == Decimal("1000")
    assert _parse_german_price("5,00 €") == Decimal("5.00")
    assert _parse_german_price("VB") is None
    assert _parse_german_price("Preis auf Anfrage") is None
    assert _parse_german_price("1.234,56 €") == Decimal("1234.56")


def test_kz_publish_not_implemented_honest_error(kz_connector):
    """Posting is a TZ-connectors-add-4 non-goal; the connector raises an
    honest error (Ч-5: no fabricated PublishResult)."""
    lot_data = {"title": "HP Omen 15", "description": "Notebook.", "item_code": "702"}
    with pytest.raises(CapabilityNotSupported, match="post"):
        kz_connector.publish(lot_data)


def test_kz_publish_still_filters_internal_fields(kz_connector):
    lot_data = {"title": "HP Omen", "description": "Popis", "price_floor": 15000}
    with pytest.raises(InternalFieldLeakageError):
        kz_connector.publish(lot_data)


def test_kz_update_and_unpublish_not_implemented(kz_connector):
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        kz_connector.update("kz-123", lot_data)
    with pytest.raises(CapabilityNotSupported, match="archive"):
        kz_connector.unpublish("kz-123")
