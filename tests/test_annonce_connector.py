"""Annonce.cz connector tests (TZ M1 A.4, «подключение по шаблону Bazoš»).

Live evidence 2026-10-04: .a/reports/m1/probes/2026-10-04/
annonce_cz_search_notebook.html (fixture: tests/fixtures/annonce_sample.html —
20 cards, 14 с ценой; вакансии/запросы без price-sticker пропускаются;
дата .ad-date в формате «29. 9. 2026» — пробел после каждой точки).
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.annonce import AnnonceConnector
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.core.config import load_config


@pytest.fixture
def annonce_connector():
    config = load_config()
    return AnnonceConnector(config.marketplaces["annonce_cz"])


def test_annonce_build_search_url(annonce_connector):
    url = annonce_connector.build_search_url(
        query="RTX 3090",
        min_price=5000,
        max_price=40000,
    )
    assert url.startswith("https://www.annonce.cz/vsechny-inzeraty$18-filter.html?")
    assert "q=RTX+3090" in url
    assert "price_from=5000" in url
    assert "price_to=40000" in url


def test_annonce_parses_sample_fixture(annonce_connector):
    """Fixture: live probe 2026-10-04, 20 cards, 14 priced (D.2: refresh
    > 30 days, → 2026-11-03). Job ads / requests (no price sticker) are
    excluded; dates from .ad-date («29. 9. 2026»)."""
    fixture_path = Path(__file__).parent / "fixtures" / "annonce_sample.html"
    listings = annonce_connector.search_with_html(fixture_path.read_text(encoding="utf-8"))
    assert len(listings) == 14
    first = listings[0]
    assert first.title == "Notebook Sony"
    assert first.price == Decimal("1700")
    assert first.currency == "CZK"
    assert first.external_url == "https://www.annonce.cz/inzerat/notebook-sony-88937515-w2e3ew.html"
    assert first.posted_at == datetime(2026, 9, 29, tzinfo=UTC)
    assert all(l.posted_at is not None for l in listings)
    assert all(l.price > 0 for l in listings)


def test_annonce_publish_not_implemented_honest_error(annonce_connector):
    """Ч-5 (TZ M1 A.4): publication is not implemented (needs the owner's
    Annonce account); the connector raises an honest error."""
    lot_data = {
        "title": "HP Omen 15",
        "description": "Herní notebook.",
        "category": "electronics",
        "item_code": "702",
    }
    with pytest.raises(CapabilityNotSupported, match="post"):
        annonce_connector.publish(lot_data)


def test_annonce_publish_still_filters_internal_fields(annonce_connector):
    lot_data = {
        "title": "HP Omen",
        "description": "Popis",
        "price_floor": 15000,
    }
    with pytest.raises(InternalFieldLeakageError):
        annonce_connector.publish(lot_data)


def test_annonce_update_and_unpublish_not_implemented(annonce_connector):
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        annonce_connector.update("an-123", lot_data)
    with pytest.raises(CapabilityNotSupported, match="archive"):
        annonce_connector.unpublish("an-123")
