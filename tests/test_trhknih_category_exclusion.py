"""TZ M1 A.5: TrhKnih is books-only — a lot of another category is NOT
offered on this marketplace (проверяется тестом; twin of mutation D.3-8:
removing the category guard from publish_lot must turn the first test RED).

The restriction lives in the marketplace registry (config
``allowed_categories`` → Marketplace row capabilities), enforced by
``publish_lot`` before any canary/credential/connector step.
"""

from decimal import Decimal
from unittest.mock import MagicMock

from sqlalchemy import select

from nextlife.connectors.base import PublishResult
from nextlife.connectors.registry import sync_marketplaces
from nextlife.core.config import load_config
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.publish import publish_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing, Lot


def _cleanup(session, item_code: str, lot_code: str) -> None:
    session.query(Listing).filter(Listing.item_code == item_code).delete()
    session.query(Lot).filter(Lot.code == lot_code).delete()
    session.query(Item).filter(Item.code == item_code).delete()
    session.commit()


def test_electronics_lot_is_not_offered_on_trhknih():
    """Mutation D.3-8 (GREEN twin): offering an «electronics» lot on TrhKnih
    → отказ with the category reason, no listing, no PUBLISHED."""
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Grafická karta RTX 3090",
            category_key="electronics",
            price=Decimal("15000.00"),
        )
        lot_code, item_code = created["lot_code"], created["item_code"]

        res = publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["trhknih_cz"],
        )
        session.commit()

        # Refused by the category guard, with the honest reason
        assert "trhknih_cz" not in res["successes"]
        assert "trhknih_cz" in res["failures"]
        assert "not allowed" in res["failures"]["trhknih_cz"]
        assert "electronics" in res["failures"]["trhknih_cz"]
        # No publication state, no listing row
        assert res["item_status"] != "PUBLISHED"
        assert res["lot_status"] != "PUBLISHED"
        listings = session.scalars(select(Listing).where(Listing.item_code == item_code)).all()
        assert len(listings) == 0

        _cleanup(session, item_code, lot_code)


def test_books_lot_passes_the_category_guard_on_trhknih():
    """The guard is category-specific: a «books» lot on TrhKnih passes the
    restriction and reaches the normal publish path. (Mock connector with
    real evidence stands in for the not-yet-implemented owner-account
    submission, same contract as the existing publish-evidence tests.)"""
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Python 3.11 Komplet",
            category_key="books",
            price=Decimal("639.00"),
        )
        lot_code, item_code = created["lot_code"], created["item_code"]

        # Registry row for trhknih_cz must exist (Listing.marketplace_key FK);
        # the sync is the real flow that creates it.
        sync_marketplaces(session, load_config())

        good_connector = MagicMock()
        good_connector.publish.return_value = PublishResult(
            external_id="tk-9999",
            external_url="https://www.trhknih.cz/kniha/tk-9999",
            evidence={
                "http_status": 200,
                "timestamp": "2026-10-04T00:00:00Z",
                "verified_snippet": "Inzerát úspěšně vložen",
            },
        )

        res = publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["trhknih_cz"],
            connector_overrides={"trhknih_cz": good_connector},
        )
        session.commit()

        # Not refused by the category guard — the books path completes
        assert "trhknih_cz" in res["successes"]
        assert res["item_status"] == "PUBLISHED"
        listing = session.scalar(select(Listing).where(Listing.item_code == item_code))
        assert listing is not None
        assert listing.external_url == "https://www.trhknih.cz/kniha/tk-9999"

        _cleanup(session, item_code, lot_code)


def test_registry_row_carries_the_category_restriction():
    """TZ M1 A.5: «В реестре площадки указывается ограничение по
    категории» — the Marketplace DB row (synced from config) carries it."""
    from nextlife.connectors.registry import _registry_capabilities
    from nextlife.core.config import load_config

    config = load_config()
    trhknih_cfg = config.marketplaces["trhknih_cz"]
    assert _registry_capabilities(trhknih_cfg)["allowed_categories"] == ["books"]
    # A marketplace without the restriction stays clean
    bazos_cfg = config.marketplaces["bazos_cz"]
    assert "allowed_categories" not in _registry_capabilities(bazos_cfg)
