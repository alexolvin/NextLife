"""Test cover photo designation and automatic propagation to marketplace listings."""

from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.status import ItemStatus, ListingStatus
from nextlife.services.listings_photos import set_item_cover_photo, get_item_ordered_photo_ids
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto, Listing
from tests.conftest import clean_database


@pytest.fixture
def clean_db_session() -> Session:
    with get_db_session() as session:
        clean_database(session)
        yield session
        clean_database(session)


def test_cover_photo_propagation(clean_db_session: Session) -> None:
    session = clean_db_session
    code = "999"

    item = Item(
        code=code,
        kind="SELL",
        title="Gaming Laptop",
        category_key="electronics",
        condition_key="used",
        attrs={},
        status=ItemStatus.READY_FOR_SALE.value,
        target_price=Decimal("1500.00"),
        target_currency="EUR",
    )
    session.add(item)
    session.flush()

    # Create 3 photos
    photo1 = ItemPhoto(
        id="p-1",
        item_code=code,
        role="ORIGINAL",
        path="item_999/p1.jpg",
        sha256="hash1",
        width=800,
        height=600,
        transform_log={},
        vlm_verdict={},
    )
    photo2 = ItemPhoto(
        id="p-2",
        item_code=code,
        role="ORIGINAL",
        path="item_999/p2.jpg",
        sha256="hash2",
        width=800,
        height=600,
        transform_log={},
        vlm_verdict={},
    )
    photo3 = ItemPhoto(
        id="p-3",
        item_code=code,
        role="ORIGINAL",
        path="item_999/p3.jpg",
        sha256="hash3",
        width=800,
        height=600,
        transform_log={},
        vlm_verdict={},
    )
    session.add_all([photo1, photo2, photo3])
    session.flush()

    # Create 2 listings for different marketplaces
    listing1 = Listing(
        id="list-1",
        item_code=code,
        marketplace_key="bazos_sk",
        price=Decimal("1500.00"),
        currency="EUR",
        price_base=Decimal("1500.00"),
        status=ListingStatus.APPROVED.value,
        photo_ids=["p-1", "p-2", "p-3"],
    )
    listing2 = Listing(
        id="list-2",
        item_code=code,
        marketplace_key="ebay",
        price=Decimal("1500.00"),
        currency="EUR",
        price_base=Decimal("1500.00"),
        status=ListingStatus.DRAFT.value,
        photo_ids=["p-1", "p-2", "p-3"],
    )
    session.add_all([listing1, listing2])
    session.commit()

    # Designate photo3 as cover
    res = set_item_cover_photo(session, code, "p-3")
    assert res["status"] == "ok"
    assert res["cover_photo_id"] == "p-3"
    session.commit()

    # Check that item.attrs has cover_photo_id
    updated_item = session.get(Item, code)
    assert updated_item.attrs.get("cover_photo_id") == "p-3"

    # Check ordered photos
    ordered = get_item_ordered_photo_ids(session, code)
    assert ordered[0] == "p-3"

    # Check that all active listings received the new photo order with p-3 at index 0
    updated_listing1 = session.get(Listing, "list-1")
    assert updated_listing1.photo_ids[0] == "p-3"

    updated_listing2 = session.get(Listing, "list-2")
    assert updated_listing2.photo_ids[0] == "p-3"
