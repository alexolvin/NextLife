"""Tests for Closed-Loop Marketplace Verification on Warehouse Item Deletion and Unpublish."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from sqlalchemy import select

from nextlife.connectors.base import CapabilityNotSupported
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.stock import StockDeletionNeedsConfirmationError, delete_stock_item
from nextlife.services.stock_deletion import (
    StockDeletionUnpublishError,
    unpublish_active_listings_closed_loop,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto, Listing, Lot, ResearchResult, ResearchRun
from tests.conftest import clean_database


@pytest.fixture
def clean_db():
    with get_db_session() as session:
        clean_database(session)


def test_delete_stock_item_aborts_when_marketplace_unpublish_fails(clean_db, monkeypatch):
    """Closed-Loop Verification: If marketplace unpublish fails, item and photos MUST NOT be deleted."""
    from nextlife.connectors import registry

    mock_conn = MagicMock()
    mock_conn.unpublish.side_effect = RuntimeError("Phonebroker lease rejected (503): Phone state: offline")
    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: mock_conn)

    with get_db_session() as session:
        created = create_item_and_lot(session, title="Test GPU Item", category_key="gpu", price=Decimal("15000"), currency="CZK")
        code = created["item_code"]

        # Add active listing
        listing = Listing(
            item_code=code,
            marketplace_key="bazos_sk",
            external_id="197224238",
            external_url="https://pc.bazos.sk/inzerat/197224238/gpu.php",
            price=Decimal("15000"),
            currency="EUR",
            price_base=Decimal("15000"),
            status="PUBLISHED",
        )
        session.add(listing)

        # Add photo
        photo = ItemPhoto(
            id="test-photo-1",
            item_code=code,
            role="ORIGINAL",
            path="/tmp/test_gpu.jpg",
            sha256="fake_sha256_hash",
            width=800,
            height=600,
        )
        session.add(photo)

        # Add research run (search)
        run = ResearchRun(
            id="test-run-1",
            item_code=code,
            status="DONE",
            marketplaces={},
            error={},
        )
        session.add(run)
        res = ResearchResult(
            id="test-res-1",
            run_id="test-run-1",
            marketplace_key="bazos_cz",
            external_url="https://bazos.cz/123",
            title="Sample GPU",
            price=Decimal("15000"),
            currency="CZK",
            price_base=Decimal("15000"),
            fx_rate_date=date.today(),
        )
        session.add(res)
        session.commit()

        # Execute delete_stock_item with confirmation
        with pytest.raises(StockDeletionUnpublishError) as exc_info:
            delete_stock_item(session, code, confirm_listings=True)

        err_text = str(exc_info.value)
        assert "Отказ в удалении предмета" in err_text
        assert "bazos_sk" in err_text
        assert "Рекомендации" in err_text

        # Verify ITEM is NOT DELETED!
        item = session.get(Item, code)
        assert item.status != "DELETED"

        # Verify PHOTO is NOT DELETED!
        photos = session.scalars(select(ItemPhoto).where(ItemPhoto.item_code == code)).all()
        assert len(photos) == 1

        # Verify SEARCH (ResearchRun) is NOT DELETED!
        runs = session.scalars(select(ResearchRun).where(ResearchRun.item_code == code)).all()
        assert len(runs) == 1

        # Verify listing is marked UNPUBLISH_FAILED
        listing_in_db = session.get(Listing, listing.id)
        assert listing_in_db.status == "UNPUBLISH_FAILED"
        assert listing_in_db.last_error_text is not None


def test_delete_stock_item_succeeds_and_cleans_subordinate_objects(clean_db, monkeypatch):
    """When marketplace unpublish succeeds, item, lot, searches, photos, and subordinate listings are cleaned."""
    from nextlife.connectors import registry

    mock_conn = MagicMock()
    mock_conn.unpublish.return_value = True
    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: mock_conn)
    # Monkeypatch reconcile check_url_is_live to return False (ad is confirmed deleted)
    monkeypatch.setattr("nextlife.services.stock_deletion.check_url_is_live", lambda url: False)

    with get_db_session() as session:
        created = create_item_and_lot(session, title="Test Laptop Item", category_key="laptops", price=Decimal("20000"), currency="CZK")
        code = created["item_code"]

        # Active listing
        listing_active = Listing(
            item_code=code,
            marketplace_key="sbazar_cz",
            external_id="sb-123",
            external_url="https://sbazar.cz/inzerat/123",
            price=Decimal("20000"),
            currency="CZK",
            price_base=Decimal("20000"),
            status="PUBLISHED",
        )
        session.add(listing_active)

        # Subordinate draft listing
        listing_draft = Listing(
            item_code=code,
            marketplace_key="bazos_cz",
            price=Decimal("20000"),
            currency="CZK",
            price_base=Decimal("20000"),
            status="DRAFT",
        )
        session.add(listing_draft)

        # Subordinate research run
        run = ResearchRun(
            id="test-run-2",
            item_code=code,
            status="DONE",
            marketplaces={},
            error={},
        )
        session.add(run)
        res = ResearchResult(
            id="test-res-2",
            run_id="test-run-2",
            marketplace_key="sbazar_cz",
            external_url="https://sbazar.cz/item",
            title="Sample Laptop",
            price=Decimal("20000"),
            currency="CZK",
            price_base=Decimal("20000"),
            fx_rate_date=date.today(),
        )
        session.add(res)
        session.commit()

        result = delete_stock_item(session, code, confirm_listings=True)
        assert result["status"] == "DELETED"

        # Item and Lot are DELETED
        item = session.get(Item, code)
        assert item.status == "DELETED"
        lot = session.scalar(select(Lot).where(Lot.code == code))
        assert lot.status == "DELETED"

        # Subordinate searches are cleaned up
        runs = session.scalars(select(ResearchRun).where(ResearchRun.item_code == code)).all()
        assert len(runs) == 0

        # Subordinate draft is deleted
        drafts = session.scalars(select(Listing).where(Listing.item_code == code, Listing.status == "DRAFT")).all()
        assert len(drafts) == 0

        # Active listing is ARCHIVED
        active = session.get(Listing, listing_active.id)
        assert active.status == "ARCHIVED"
