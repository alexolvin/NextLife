"""Tests for pickup address and tracking number release gating (T-061, Section III.3.3).

DoD:
- Pickup address and tracking number release blocked before PAID.
- Exception: Cash upon personal meeting allows address release upon RESERVED.
- Exception: Dobírka (COD) allows tracking number release upon dispatch.
- Attempt to release address before PAID is blocked by outgoing filter (InternalFieldLeakageError).
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.services.fulfillment import (
    FulfillmentSecurityError,
    release_pickup_address,
    release_tracking_number,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Listing, Lot, Party


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def _create_deal(session, payment_method="BANK_TRANSFER", status="RESERVED"):
    created = create_item_and_lot(
        session=session,
        title="Fulfillment Test Item",
        category_key="general",
        price=Decimal("4500.00"),
    )
    lot_code = created["lot_code"]
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))

    party = Party(
        id=str(uuid.uuid4()),
        name="Lukas Buyer",
        email="lukas@example.com",
    )
    session.add(party)

    listing = Listing(
        id=str(uuid.uuid4()),
        item_code=created["item_code"],
        marketplace_key="bazos_cz",
        external_id=f"bazos-{lot_code}",
        external_url=f"https://bazos.cz/item/{lot_code}",
        price=Decimal("4500.00"),
        currency="CZK",
        price_base=Decimal("4500.00"),
        status="PUBLISHED",
        published_at=datetime.now(UTC),
    )
    session.add(listing)
    session.flush()

    deal = Deal(
        id=str(uuid.uuid4()),
        lot_id=lot.id,
        party_id=party.id,
        listing_id=listing.id,
        agreed_price=Decimal("4500.00"),
        currency="CZK",
        price_base=Decimal("4500.00"),
        delivery_method="PERSONAL" if "CASH" in payment_method else "ZASILKOVNA",
        payment_method=payment_method,
        status=status,
    )
    session.add(deal)
    session.flush()
    return lot, deal


def test_pickup_address_release_blocked_before_paid_for_bank_transfer(db):
    """DoD: Attempt to send address before PAID is blocked by outgoing filter."""
    lot, deal = _create_deal(db, payment_method="BANK_TRANSFER", status="RESERVED")

    with pytest.raises(InternalFieldLeakageError, match="before payment is confirmed"):
        release_pickup_address(
            session=db,
            deal_id=deal.id,
            address="Pražská 42, Praha 1",
        )

    # Status unchanged
    db.refresh(deal)
    assert deal.handover is None


def test_pickup_address_release_allowed_after_paid_for_bank_transfer(db):
    lot, deal = _create_deal(db, payment_method="BANK_TRANSFER", status="PAID")

    res = release_pickup_address(
        session=db,
        deal_id=deal.id,
        address="Pražská 42, Praha 1",
        instructions="Zvonek č. 4, 2. patro",
    )

    assert res["address"] == "Pražská 42, Praha 1"
    assert "Pražská 42, Praha 1" in res["message"]
    assert f"[{lot.code}]" in res["message"]

    db.refresh(deal)
    assert deal.handover is not None
    assert deal.handover.place == "Pražská 42, Praha 1"


def test_pickup_address_exception_allowed_for_cash_on_pickup_in_reserved(db):
    """DoD: Exception for cash upon personal meeting allows address release upon RESERVED."""
    lot, deal = _create_deal(db, payment_method="CASH_ON_PICKUP", status="RESERVED")

    res = release_pickup_address(
        session=db,
        deal_id=deal.id,
        address="Náměstí Míru 5, Praha 2",
    )

    assert res["address"] == "Náměstí Míru 5, Praha 2"
    assert "Náměstí Míru 5, Praha 2" in res["message"]

    db.refresh(deal)
    assert deal.handover is not None
    assert deal.handover.place == "Náměstí Míru 5, Praha 2"


def test_tracking_number_blocked_before_paid_for_prepaid(db):
    """DoD: Tracking number release blocked before PAID for prepayment."""
    lot, deal = _create_deal(db, payment_method="BANK_TRANSFER", status="RESERVED")

    with pytest.raises(FulfillmentSecurityError, match="before payment is confirmed"):
        release_tracking_number(
            session=db,
            deal_id=deal.id,
            tracking_number="Z1234567890",
        )


def test_tracking_number_allowed_after_paid_for_prepaid(db):
    lot, deal = _create_deal(db, payment_method="BANK_TRANSFER", status="PAID")

    res = release_tracking_number(
        session=db,
        deal_id=deal.id,
        tracking_number="Z1234567890",
        carrier="Zásilkovna",
        tracking_url="https://tracking.packeta.com/cs/Z1234567890",
    )

    assert res["tracking_number"] == "Z1234567890"
    assert "Z1234567890" in res["message"]
    assert f"[{lot.code}]" in res["message"]

    db.refresh(deal)
    assert deal.handover is not None
    assert deal.handover.tracking_number == "Z1234567890"
    assert deal.handover.status == "DISPATCHED"


def test_tracking_number_exception_allowed_for_cod_before_paid(db):
    """DoD: Exception for Dobírka (COD) allows tracking number release upon dispatch."""
    lot, deal = _create_deal(db, payment_method="COD", status="RESERVED")

    res = release_tracking_number(
        session=db,
        deal_id=deal.id,
        tracking_number="PPL987654321",
        carrier="PPL",
    )

    assert res["tracking_number"] == "PPL987654321"
    assert "PPL987654321" in res["message"]

    db.refresh(deal)
    assert deal.handover is not None
    assert deal.handover.tracking_number == "PPL987654321"
    assert deal.handover.status == "DISPATCHED"
