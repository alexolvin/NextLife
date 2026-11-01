"""Tests for payment confirmation rules and screenshot proof rejection (T-060, Section III.3.3).

DoD:
- Transition to PAID allowed only for OWNER or MARKETPLACE with verifiable evidence.
- AGENT is strictly forbidden from initiating PAID.
- Buyer message with screenshot attachment strictly does NOT transition to PAID.
- Buyer screenshot generates escalation to owner for manual bank verification.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.payment_confirmation import (
    PaymentConfirmationError,
    UnauthorizedPaymentConfirmationError,
    confirm_payment,
    handle_buyer_payment_notification,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent, Deal, Listing, Lot, Message, Party, Thread


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def _create_deal_fixture(session, price=Decimal("6000.00")):
    created = create_item_and_lot(
        session=session,
        title="Payment Confirmation Item",
        category_key="general",
        price=price,
    )
    lot_code = created["lot_code"]
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))

    buyer_id = f"buyer-{uuid.uuid4().hex[:8]}"
    party = Party(id=buyer_id, name="Petr Buyer", email="petr@example.com")
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
        payment_method="BANK_TRANSFER",
        status="RESERVED",
    )
    session.add(deal)
    session.flush()

    thread = Thread(
        id=str(uuid.uuid4()),
        listing_id=listing.id,
        channel="bazos_cz",
        counterparty="petr@example.com",
        lang="cs",
        status="ACTIVE",
    )
    session.add(thread)
    session.flush()

    msg = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        direction="IN",
        transport="BAZOS_CZ",
        text_original="Dobrý den, posílám potvrzení o platbě v příloze.",
        lang_original="cs",
        sent_at=datetime.now(UTC),
    )
    session.add(msg)
    session.flush()

    return lot, deal, thread, msg


def test_owner_can_confirm_payment(db):
    lot, deal, thread, msg = _create_deal_fixture(db)

    res = confirm_payment(
        session=db,
        deal_id=deal.id,
        actor="OWNER",
        evidence={"bank_statement_id": "TX-998822", "amount": "6000.00"},
    )
    assert res["status"] == "PAID"
    assert res["confirmed_by"] == "OWNER"

    db.refresh(deal)
    db.refresh(lot)
    assert deal.status == "PAID"
    assert lot.status == "PAID"
    assert deal.closure_evidence["payment_confirmed_by"] == "OWNER"


def test_marketplace_can_confirm_payment_with_evidence(db):
    lot, deal, thread, msg = _create_deal_fixture(db)

    res = confirm_payment(
        session=db,
        deal_id=deal.id,
        actor="MARKETPLACE",
        evidence={"marketplace": "aukro_cz", "payment_status": "PAID", "escrow_id": "ESC-1234"},
    )
    assert res["status"] == "PAID"
    assert res["confirmed_by"] == "MARKETPLACE"

    db.refresh(deal)
    assert deal.status == "PAID"


def test_marketplace_without_paid_evidence_fails(db):
    lot, deal, thread, msg = _create_deal_fixture(db)

    with pytest.raises(PaymentConfirmationError, match="verifiable evidence"):
        confirm_payment(
            session=db,
            deal_id=deal.id,
            actor="MARKETPLACE",
            evidence={"marketplace": "aukro_cz", "payment_status": "PENDING"},
        )


def test_agent_strictly_forbidden_from_confirming_payment(db):
    """DoD: Agent cannot transition deal to PAID."""
    lot, deal, thread, msg = _create_deal_fixture(db)

    with pytest.raises(UnauthorizedPaymentConfirmationError, match="Agent is strictly prohibited"):
        confirm_payment(
            session=db,
            deal_id=deal.id,
            actor="AGENT",
            evidence={"vlm_check": "passed"},
        )

    db.refresh(deal)
    assert deal.status == "RESERVED", "Deal must not transition to PAID on agent attempt"


def test_buyer_screenshot_does_not_transition_to_paid_and_escalates(db):
    """DoD: Counterparty message with payment screenshot does NOT mark PAID and creates escalation."""
    lot, deal, thread, msg = _create_deal_fixture(db)

    res = handle_buyer_payment_notification(
        session=db,
        deal_id=deal.id,
        message_id=msg.id,
        has_screenshot=True,
        attachment_name="platba_screenshot.png",
    )

    # 1. Verify deal is NOT paid!
    assert res["paid"] is False
    assert res["escalated_to_owner"] is True

    db.refresh(deal)
    assert deal.status != "PAID"
    assert deal.status == "RESERVED"

    # 2. Verify escalation created for OWNER
    db.refresh(msg)
    assert msg.escalated is True
    assert msg.answered_by is None
    ev = msg.source_evidence or {}
    assert ev.get("escalation_status") == "AWAITING_OWNER_COMMAND"
    assert ev.get("escalation_reason") == "buyer_payment_screenshot_unverified"

    # 3. Verify draft response NEVER says "payment received"
    draft = ev.get("prepared_response", "")
    assert "platbu ověřím" in draft.lower()
    assert "platba byla přijata" not in draft.lower()
    assert "platba přijata" not in draft.lower()

    # 4. Verify audit event for screenshot rejection
    audits = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "PAYMENT_SCREENSHOT_REJECTED_AS_PROOF",
            AuditEvent.entity_id == deal.id,
        )
    ).all()
    assert len(audits) >= 1
    assert audits[-1].data["has_screenshot"] is True
