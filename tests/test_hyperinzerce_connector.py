"""Hyperinzerce.cz connector tests (TZ M1 A.4, «подключение по шаблону Bazoš»).

Live evidence 2026-10-04: .a/reports/m1/probes/2026-10-04/
hyperinzerce_cz_search_notebook.html (fixture: tests/fixtures/
hyperinzerce_sample.html — 20 cards, 15 с цифрой в цене; «Dohodou»,
«V textu», «Nejvyšší nabídka» без цены пропускаются; дат на карточках НЕТ).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.core.config import load_config


@pytest.fixture
def hyperinzerce_connector():
    from nextlife.connectors.hyperinzerce import HyperinzerceConnector

    config = load_config()
    return HyperinzerceConnector(config.marketplaces["hyperinzerce_cz"])


def test_hyperinzerce_build_search_url(hyperinzerce_connector):
    url = hyperinzerce_connector.build_search_url(
        query="RTX 3090",
        min_price=10000,
        max_price=30000,
    )
    assert url.startswith("https://hyperinzerce.cz/inzeraty/Index?")
    assert "query=RTX+3090" in url
    assert "PriceFrom=10000" in url
    assert "PriceTo=30000" in url


def test_hyperinzerce_parses_sample_fixture(hyperinzerce_connector):
    """Fixture: live probe 2026-10-04, 20 cards, 15 priced (D.2: refresh > 30
    days, → 2026-11-03). Ч-1: no date on search cards → posted_at is None."""
    fixture_path = Path(__file__).parent / "fixtures" / "hyperinzerce_sample.html"
    listings = hyperinzerce_connector.search_with_html(fixture_path.read_text(encoding="utf-8"))
    assert len(listings) == 15
    first = listings[0]
    assert "Notebooky pro všechny" in first.title
    assert first.price == Decimal("4444")
    assert first.currency == "CZK"
    assert first.external_url.startswith("https://pocitace.hyperinzerce.cz/notebooky/inzerat/")
    assert all(l.posted_at is None for l in listings)
    assert all(l.price > 0 for l in listings)


def test_hyperinzerce_publish_not_implemented_honest_error(hyperinzerce_connector):
    """Ч-5 (TZ M1 A.4): publication is not implemented (needs the owner's
    Hyperinzerce account); the connector raises an honest error."""
    lot_data = {
        "title": "Lenovo ThinkPad T14s",
        "description": "Notebook v perfektním stavu.",
        "category": "electronics",
        "item_code": "701",
    }
    with pytest.raises(CapabilityNotSupported, match="post"):
        hyperinzerce_connector.publish(lot_data)


def test_hyperinzerce_publish_still_filters_internal_fields(hyperinzerce_connector):
    lot_data = {
        "title": "Lenovo ThinkPad",
        "description": "Popis",
        "price_floor": 10000,
    }
    with pytest.raises(InternalFieldLeakageError):
        hyperinzerce_connector.publish(lot_data)


def test_hyperinzerce_update_and_unpublish_not_implemented(hyperinzerce_connector):
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        hyperinzerce_connector.update("hi-123", lot_data)
    with pytest.raises(CapabilityNotSupported, match="archive"):
        hyperinzerce_connector.unpublish("hi-123")
