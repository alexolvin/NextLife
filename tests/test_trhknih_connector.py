"""TrhKnih.cz connector tests (TZ M1 A.5, «подключение по шаблону Bazoš»).

Live evidence 2026-10-04: .a/reports/m1/probes/2026-10-04/
trhknih_cz_search_python.html (fixture: tests/fixtures/trhknih_sample.html —
10 rows .serp-item, 5 с ценой; строки-теги и карточки без price-sticker
исключаются базовым парсером; дат публикации на карточках НЕТ — Ч-1).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.core.config import load_config


@pytest.fixture
def trhknih_connector():
    from nextlife.connectors.trhknih import TrhKnihConnector

    config = load_config()
    return TrhKnihConnector(config.marketplaces["trhknih_cz"])


def test_trhknih_build_search_url(trhknih_connector):
    """No price params: the site's search page has no price filter (Ч-6:
    only live-verified parameters are declared in the config route map)."""
    url = trhknih_connector.build_search_url(query="RTX 3090")
    assert url == "https://www.trhknih.cz/hledat?q=RTX+3090"


def test_trhknih_registry_declares_books_only(trhknih_connector):
    """TZ M1 A.5: the marketplace registry carries the category restriction."""
    assert trhknih_connector.config.allowed_categories == ["books"]
    caps = trhknih_connector.config.capabilities
    assert caps["search"] is True
    assert caps["post"] is True
    assert caps["edit"] is True
    assert caps["archive"] is True


def test_trhknih_parses_sample_fixture(trhknih_connector):
    """Fixture: live probe 2026-10-04 (D.2: refresh > 30 days, → 2026-11-03).

    10 rows .serp-item, 5 with a price sticker; tag rows and book rows
    without a sticker are excluded. Ч-1: no publication date on the cards
    (only the print year) → posted_at stays None.
    """
    fixture_path = Path(__file__).parent / "fixtures" / "trhknih_sample.html"
    listings = trhknih_connector.search_with_html(fixture_path.read_text(encoding="utf-8"))
    assert len(listings) == 5
    first = listings[0]
    assert first.title == "Python"
    assert first.price == Decimal("639")
    assert first.currency == "CZK"
    assert first.external_url == "https://www.trhknih.cz/kniha/1c0gpdrroe"
    assert all(l.posted_at is None for l in listings)
    assert all(l.price > 0 for l in listings)


def test_trhknih_publish_books_enforcement_and_dry_run(trhknih_connector):
    """TrhKnih enforces books category and allows dry-run publishing."""
    from nextlife.connectors.base import ConnectorError

    # Non-book lot is rejected
    non_book = {
        "title": "GeForce RTX 3080",
        "description": "Grafická karta.",
        "category": "gpu",
        "item_code": "704",
    }
    with pytest.raises(ConnectorError, match="принимает исключительно книги"):
        trhknih_connector.publish(non_book, dry_run=True)

    # Book lot succeeds in dry-run
    book_lot = {
        "title": "Kniha Python 3.11 Komplet",
        "description": "Knihy v dobrom stavu.",
        "category": "books",
        "item_code": "703",
    }
    dry_res = trhknih_connector.publish(book_lot, dry_run=True)
    assert dry_res.external_id.startswith("dry_trhknih_")
    assert dry_res.evidence["dry_run"] is True

    # Live run without credentials requires auth
    with pytest.raises(ConnectorError, match="авторизация"):
        trhknih_connector.publish(book_lot, dry_run=False)


def test_trhknih_publish_still_filters_internal_fields(trhknih_connector):
    lot_data = {
        "title": "Python 3.11",
        "description": "Popis",
        "price_floor": 500,
    }
    with pytest.raises(InternalFieldLeakageError):
        trhknih_connector.publish(lot_data)


def test_trhknih_update_and_unpublish_implemented(trhknih_connector):
    lot_data = {"title": "Kniha Python Updated", "description": "Updated desc", "category": "books"}
    res = trhknih_connector.update("tk-123", lot_data)
    assert res.evidence["dry_run"] is True
    assert trhknih_connector.unpublish("tk-123") is True
