"""Tests for Dobírka risk calculation and uncollected return processing (T-062, Section III.3.4).

DoD:
- Owner receives loss calculation before dispatch.
- When parcel is uncollected by buyer, deal transitions to RETURNED.
- Return shipping, forward shipping, COD fee, and packaging costs are fixed in settlement.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.services.dobirka import (
    DobirkaError,
    calculate_cod_risk,
    generate_cod_pre_dispatch_warning,
    handle_cod_uncollected,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    Deal,
    Handover,
    Listing,
    Lot,
    Outbox,
    Party,
)


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def _create_cod_deal_fixture(session, price=Decimal("3500.00")):
    created = create_item_and_lot(
        session=session,
        title="Dobirka Test Lot",
        category_key="general",
        price=price,
    )
    lot_code = created["lot_code"]
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))

    party = Party(
        id=str(uuid.uuid4()),
        name="Jan Novak",
        email="jan.novak@example.cz",
    )
    session.add(party)

    listing = Listing(
        id=str(uuid.uuid4()),
        item_code=created["item_code"],
        marketplace_key="bazos_cz",
        external_id=f"bazos-{lot_code}",
        external_url=f"https://bazos.cz/item/{lot_code}",
        price=price,
        currency="CZK",
        price_base=price,
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
        agreed_price=price,
        currency="CZK",
        price_base=price,
        delivery_method="ZASILKOVNA",
        payment_method="COD",
        status="RESERVED",
    )
    session.add(deal)

    handover = Handover(
        id=str(uuid.uuid4()),
        deal_id=deal.id,
        method="ZASILKOVNA",
        status="DISPATCHED",
        tracking_number="Z123456789",
    )
    session.add(handover)
    session.flush()

    return lot, deal, party


def test_calculate_cod_risk_math():
    risk = calculate_cod_risk(
        forward_shipping=Decimal("89.00"),
        return_shipping=Decimal("89.00"),
        cod_fee=Decimal("25.00"),
        packaging_cost=Decimal("35.00"),
    )
    # 89 + 89 + 25 + 35 = 238
    assert risk["forward_shipping"] == Decimal("89.00")
    assert risk["return_shipping"] == Decimal("89.00")
    assert risk["cod_fee"] == Decimal("25.00")
    assert risk["packaging_cost"] == Decimal("35.00")
    assert risk["total_risk_loss"] == Decimal("238.00")


def test_owner_receives_warning_before_dispatch(db):
    """DoD: Owner receives loss calculation before dispatch."""
    lot, deal, party = _create_cod_deal_fixture(db)

    res = generate_cod_pre_dispatch_warning(
        session=db,
        deal_id=deal.id,
        forward_shipping=Decimal("89.00"),
        return_shipping=Decimal("89.00"),
        cod_fee=Decimal("25.00"),
        packaging_cost=Decimal("35.00"),
        notify_owner=True,
    )

    assert res["risk_breakdown"]["total_risk_loss"] == Decimal("238.00")
    assert "238.00" in res["warning_text"]
    assert f"[{lot.code}]" in res["warning_text"]

    # Verify notification queued in Outbox for OWNER
    outbox_entry = db.scalar(
        select(Outbox).where(Outbox.idempotency_key == f"cod-warning-{deal.id}")
    )
    assert outbox_entry is not None
    assert outbox_entry.recipient_hint == "OWNER"
    assert "238.00" in outbox_entry.body

    # Verify risk calculation stored in deal closure evidence
    db.refresh(deal)
    assert "cod_risk_calculation" in deal.closure_evidence
    assert deal.closure_evidence["cod_risk_calculation"]["total_risk_loss"] == "238.00"


def test_handle_cod_uncollected_transitions_deal_and_settles_expenses(db):
    """DoD: When uncollected, deal transitions to RETURNED and expenses are fixed in settlement."""
    lot, deal, party = _create_cod_deal_fixture(db)

    # First calculate pre-dispatch risk
    generate_cod_pre_dispatch_warning(
        session=db,
        deal_id=deal.id,
        forward_shipping=Decimal("89.00"),
        return_shipping=Decimal("89.00"),
        cod_fee=Decimal("25.00"),
        packaging_cost=Decimal("35.00"),
    )

    # Carrier reports package uncollected / returned
    res = handle_cod_uncollected(
        session=db,
        deal_id=deal.id,
        return_shipping=Decimal("89.00"),
        restore_lot_to_published=False,
    )

    assert res["status"] == "RETURNED"
    assert res["total_shipping_loss"] == Decimal("203.00")  # 89 + 89 + 25
    assert res["packaging_cost"] == Decimal("35.00")
    assert res["net_loss"] == Decimal("238.00")
    assert res["settlement_net_payout"] == Decimal("-238.00")

    # Verify DB state of deal
    db.refresh(deal)
    assert deal.status == "RETURNED"
    assert deal.closure_evidence["return_reason"] == "COD_UNCOLLECTED_BY_BUYER"
    assert deal.handover.status == "RETURNED"

    # Verify Settlement record
    settlement = deal.settlement
    assert settlement is not None
    assert settlement.gross_amount == Decimal("0.00")
    assert settlement.delivery_cost == Decimal("203.00")
    assert settlement.packaging_cost == Decimal("35.00")
    assert settlement.net_payout == Decimal("-238.00")
    assert settlement.payout_status == "SETTLED"

    # Verify lot state
    db.refresh(lot)
    assert lot.status == "RETURNED"


def test_handle_cod_uncollected_with_restore_to_published(db):
    lot, deal, party = _create_cod_deal_fixture(db)
    generate_cod_pre_dispatch_warning(
        session=db,
        deal_id=deal.id,
        forward_shipping=Decimal("79.00"),
        cod_fee=Decimal("20.00"),
        packaging_cost=Decimal("20.00"),
    )

    res = handle_cod_uncollected(
        session=db,
        deal_id=deal.id,
        restore_lot_to_published=True,
    )

    assert res["lot_status"] == "PUBLISHED"
    db.refresh(lot)
    assert lot.status == "PUBLISHED"
    assert lot.reserved_for_party_id is None


def test_non_cod_deal_raises_error(db):
    lot, deal, party = _create_cod_deal_fixture(db)
    deal.payment_method = "BANK_TRANSFER"
    db.flush()

    with pytest.raises(DobirkaError, match="not configured for COD"):
        generate_cod_pre_dispatch_warning(
            session=db,
            deal_id=deal.id,
            forward_shipping=Decimal("89.00"),
        )
