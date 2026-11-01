"""Tests for Dispute Management, Dossier Assembly & Neutral Auto-Reply (TZ v2.0 Section III.7 / Task T-068).

DoD:
- Full dossier assembly with chronology, listings, dispatch evidence, handover act.
- Zero promises, admissions of fault, or evaluations in auto-acknowledgment.
- Test against list of forbidden expressions in Russian, Czech, and English.
- Settlement funds held, truth logs asserted.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from nextlife.deals.handover_protocol import generate_handover_protocol
from nextlife.services.dispatch_evidence import submit_dispatch_evidence
from nextlife.services.dispute import (
    FORBIDDEN_DISPUTE_EXPRESSIONS,
    DisputePromiseForbiddenError,
    assemble_dispute_dossier,
    open_dispute,
    render_neutral_acknowledgment,
    validate_neutral_dispute_response,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    Deal,
    Handover,
    Item,
    Listing,
    Lot,
    Message,
    Outbox,
    Party,
    Settlement,
    Thread,
)


def test_neutral_dispute_response_contains_no_forbidden_promises():
    # 1. Test standard neutral templates (CS & RU)
    cs_ack = render_neutral_acknowledgment(lot_code="GPU-123", lang="cs")
    ru_ack = render_neutral_acknowledgment(lot_code="GPU-123", lang="ru")

    assert "prošetření" in cs_ack
    assert "на рассмотрение" in ru_ack

    # Test against EVERY single forbidden expression in the catalog
    for forbidden in FORBIDDEN_DISPUTE_EXPRESSIONS:
        assert forbidden.lower() not in cs_ack.lower(), f"Forbidden phrase '{forbidden}' found in Czech response!"
        assert forbidden.lower() not in ru_ack.lower(), f"Forbidden phrase '{forbidden}' found in Russian response!"


def test_forbidden_expressions_validator_raises_error():
    # Test that forbidden expressions are caught and blocked
    for forbidden in FORBIDDEN_DISPUTE_EXPRESSIONS:
        sample_bad_text = f"Здравствуйте, мы {forbidden} и всё уладим."
        with pytest.raises(DisputePromiseForbiddenError):
            validate_neutral_dispute_response(sample_bad_text)


def test_assemble_dispute_dossier_and_open_dispute():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Dispute Test Hardware",
            category_key="gpu",
            price=Decimal("11000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        lot = session.query(Lot).filter(Lot.code == lot_code).first()

        party = Party(
            id=str(uuid.uuid4()),
            name="Claiming Buyer",
            email="dispute.buyer@example.cz",
        )
        session.add(party)

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key="bazos_cz",
            external_id=f"bazos-{lot_code}",
            external_url="https://bazos.cz/item/disp",
            price=Decimal("11000.00"),
            currency="CZK",
            price_base=Decimal("11000.00"),
            status="PUBLISHED",
        )
        session.add(listing)
        session.flush()

        deal = Deal(
            id=str(uuid.uuid4()),
            lot_id=lot.id,
            party_id=party.id,
            listing_id=listing.id,
            agreed_price=Decimal("11000.00"),
            currency="CZK",
            price_base=Decimal("11000.00"),
            delivery_method="ZASILKOVNA",
            payment_method="BANK_TRANSFER",
            status="PAID",
        )
        session.add(deal)

        settlement = Settlement(
            id=str(uuid.uuid4()),
            deal_id=deal.id,
            gross_amount=Decimal("11000.00"),
            net_payout=Decimal("10500.00"),
            currency="CZK",
            payout_status="PENDING",
        )
        session.add(settlement)

        thread = Thread(
            id=str(uuid.uuid4()),
            listing_id=listing.id,
            channel="MARKETPLACE",
            counterparty="Claiming Buyer",
            lang="cs",
            status="ACTIVE",
        )
        session.add(thread)
        session.flush()

        msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            direction="IN",
            transport="MARKETPLACE",
            text_original="Karta nefunguje, chci vrátit peníze!",
            lang_original="cs",
            sent_at=datetime.now(UTC),
        )
        session.add(msg)
        session.commit()

        # Submit 1 dispatch evidence and generate act
        submit_dispatch_evidence(session, deal.id, "box_content_photo", "/data/evidence/box.jpg")
        generate_handover_protocol(session, deal.id, actor="OWNER")
        session.commit()

        try:
            # 1. Verify Dossier Assembly
            dossier = assemble_dispute_dossier(session, deal.id)
            assert dossier["deal_id"] == deal.id
            assert dossier["lot_code"] == lot_code
            assert len(dossier["chronology"]) >= 1
            assert dossier["chronology"][0]["text"] == "Karta nefunguje, chci vrátit peníze!"
            assert dossier["has_handover_act"] is True
            assert "box_content_photo" in dossier["dispatch_evidence"]["present"]

            # 2. Open Dispute
            res = open_dispute(
                session=session,
                deal_id=deal.id,
                reason="Buyer claims item is not working",
                thread_id=thread.id,
                actor="BUYER",
            )
            session.commit()

            # Verify deal status is DISPUTED
            saved_deal = session.get(Deal, deal.id)
            assert saved_deal.status == "DISPUTED"

            # Verify settlement payout is HELD
            saved_settlement = session.get(Settlement, settlement.id)
            assert saved_settlement.payout_status == "HELD"

            # Verify neutral message in thread
            out_msg = session.query(Message).filter(Message.thread_id == thread.id, Message.direction == "OUT").first()
            assert out_msg is not None
            assert "prošetření" in out_msg.text_original
            validate_neutral_dispute_response(out_msg.text_original)

            # Verify owner notification with options
            owner_outbox = session.get(Outbox, res["owner_notification_id"])
            assert owner_outbox is not None
            assert "REJECT_WITH_ACT" in owner_outbox.body
            assert "PARTIAL_REFUND" in owner_outbox.body
            assert "FULL_RETURN" in owner_outbox.body
        finally:
            # Cleanup
            session.query(Outbox).filter(Outbox.recipient_hint == "OWNER").delete()
            session.query(Message).filter(Message.thread_id == thread.id).delete()
            session.query(Thread).filter(Thread.id == thread.id).delete()
            session.query(Settlement).filter(Settlement.deal_id == deal.id).delete()
            session.query(Handover).filter(Handover.deal_id == deal.id).delete()
            session.query(Deal).filter(Deal.id == deal.id).delete()
            session.query(Listing).filter(Listing.id == listing.id).delete()
            session.query(Party).filter(Party.id == party.id).delete()
            session.query(Lot).filter(Lot.code == lot_code).delete()
            session.query(Item).filter(Item.code == item_code).delete()
            session.commit()
