import uuid
from decimal import Decimal
import pytest
from sqlalchemy import select

from nextlife.core.status import ItemStatus, ListingStatus
from nextlife.services.listings import ListingDeletionForbiddenError, delete_listing
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing, Lot
from tests.conftest import clean_database


@pytest.fixture
def clean_db():
    with get_db_session() as session:
        clean_database(session)


def test_delete_listing_forbidden_on_published(clean_db):
    """Listing in PUBLISHED status must NOT be deleted from the system."""
    with get_db_session() as session:
        created = create_item_and_lot(session, title="Test GPU Item", category_key="gpu", price=Decimal("15000"), currency="CZK")
        code = created["item_code"]

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=code,
            marketplace_key="bazos_sk",
            title="Active GPU Ad",
            description="Active ad",
            price=Decimal("15000"),
            currency="EUR",
            price_base=Decimal("15000"),
            status=ListingStatus.PUBLISHED.value,
            external_url="https://pc.bazos.sk/inzerat/123/active.php",
        )
        session.add(listing)
        session.commit()

        with pytest.raises(ListingDeletionForbiddenError) as exc_info:
            delete_listing(session, listing.id)

        assert "активно на площадке" in str(exc_info.value)
        # Ensure listing is still in the database!
        remaining = session.get(Listing, listing.id)
        assert remaining is not None
        assert remaining.status == ListingStatus.PUBLISHED.value


def test_delete_listing_forbidden_on_unpublish_failed(clean_db):
    """Listing in UNPUBLISH_FAILED status must NOT be deleted from the system."""
    with get_db_session() as session:
        created = create_item_and_lot(session, title="Test GPU Item 2", category_key="gpu", price=Decimal("12000"), currency="CZK")
        code = created["item_code"]

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=code,
            marketplace_key="bazos_sk",
            title="Failed Unpublish Ad",
            description="Phone was offline",
            price=Decimal("12000"),
            currency="EUR",
            price_base=Decimal("12000"),
            status=ListingStatus.UNPUBLISH_FAILED.value,
            external_url="https://pc.bazos.sk/inzerat/456/failed.php",
        )
        session.add(listing)
        session.commit()

        with pytest.raises(ListingDeletionForbiddenError) as exc_info:
            delete_listing(session, listing.id)

        assert "активно на площадке" in str(exc_info.value)
        assert session.get(Listing, listing.id) is not None


def test_delete_listing_allowed_on_archived(clean_db):
    """Listing in ARCHIVED status (offline) is allowed to be deleted."""
    with get_db_session() as session:
        created = create_item_and_lot(session, title="Test GPU Item 3", category_key="gpu", price=Decimal("9000"), currency="CZK")
        code = created["item_code"]

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=code,
            marketplace_key="bazos_sk",
            title="Archived Ad",
            description="Old ad",
            price=Decimal("9000"),
            currency="EUR",
            price_base=Decimal("9000"),
            status=ListingStatus.ARCHIVED.value,
        )
        session.add(listing)
        session.commit()

        delete_listing(session, listing.id)
        session.commit()

        assert session.get(Listing, listing.id) is None


def test_delete_listing_allowed_on_draft(clean_db):
    """Listing in DRAFT status without external listing is allowed to be deleted."""
    with get_db_session() as session:
        created = create_item_and_lot(session, title="Test GPU Item 4", category_key="gpu", price=Decimal("5000"), currency="CZK")
        code = created["item_code"]

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=code,
            marketplace_key="sbazar_cz",
            title="Draft Ad",
            description="Draft",
            price=Decimal("5000"),
            currency="CZK",
            price_base=Decimal("5000"),
            status=ListingStatus.DRAFT.value,
        )
        session.add(listing)
        session.commit()

        delete_listing(session, listing.id)
        session.commit()

        assert session.get(Listing, listing.id) is None
