"""Tests for Fraud Schemes Catalog and Security Detection (T-052, Section III.9).

DoD:
- Dedicated individual test for each of the 9 starting fraud schemes.
- Substantive auto-reply is strictly blocked (can_auto_reply = False) on every fraud scheme.
- Legitimate messages are not falsely flagged.
- DB tagging, AuditEvent, and TruthLog recorded upon detection.
- Module length <= 400 lines.
"""

import uuid

from sqlalchemy import select

from nextlife.comms.fraud import (
    detect_fraud,
    process_incoming_message_security,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent, Item, Listing, Lot, Message, Thread, TruthLog


def test_scheme_1_payment_link():
    text = "Dobrý den, poslal jsem peníze, zde je potvrzení platby a odkaz pro vyzvednout peníze: https://dpd-platba.cz/receive"
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id in ("payment_link", "fake_escrow_service")
    assert res.prepared_refusal is not None


def test_scheme_2_card_credentials():
    text = "Dobrý den, pro autorizaci platby mi prosím pošlete číslo karty, datum expirace a CVV kód."
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id == "card_credentials"
    assert "cvv" in " ".join(res.matched_indicators).lower()


def test_scheme_3_fake_escrow_service():
    text = "Koupím to přes bezpečná platba Zásilkovna. Zde potvrďte doručení a platbu: https://zasilkovna-platba.cz/order/99"
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id in ("fake_escrow_service", "payment_link")


def test_scheme_4_external_messenger_redirect():
    text = "Mám vážný zájem. Na Bazoši moc nejsem, napište na whatsapp: https://wa.me/420777123456"
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id == "external_messenger_redirect"


def test_scheme_5_overpayment_refund():
    text = "Dobrý den, omylem jsem poslal více peněz za položku. Vraťte mi prosím přeplatek na účet."
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id == "overpayment_refund"


def test_scheme_6_advance_shipment_claim():
    text = "Peníze jsou na cestě, banka to zpracovává. Odešlete prosím balík ihned ještě dnes."
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id == "advance_shipment_claim"


def test_scheme_7_payment_screenshot():
    text = "Tady je screenshot o platbě z mobilní banky. Můžete zboží expedovat."
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id == "payment_screenshot"


def test_scheme_8_fake_courier_pickup():
    text = "Kurýr DPD zítra přijede k vám domů, peníze v obálce předá a sám vše zařídí."
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id == "fake_courier_pickup"


def test_scheme_9_urgency_pressure():
    text = "Spěchá to, rozhoduji se teď hned! Musíte to okamžitě poslat, jinak nabídka propadá!"
    res = detect_fraud(text)
    assert res.is_fraud_suspected is True
    assert res.can_auto_reply is False
    assert res.scheme_id == "urgency_pressure"


def test_legitimate_buyer_message_is_not_flagged():
    text = "Dobrý den, je prosím tato položka stále dostupná? Rád bych se domluvil na osobním převzetí v Praze."
    res = detect_fraud(text)
    assert res.is_fraud_suspected is False
    assert res.can_auto_reply is True
    assert res.scheme_id is None


def test_db_processing_tags_message_and_records_audit():
    with get_db_session() as session:
        from nextlife.services.lot_creation import create_item_and_lot
        created = create_item_and_lot(
            session=session,
            title="Security Test Item",
            category_key="general",
            price=1000,
        )
        item_code = created["item_code"]
        lot_code = created["lot_code"]

        listing_id = str(uuid.uuid4())
        listing = Listing(
            id=listing_id,
            item_code=item_code,
            marketplace_key="bazos_cz",
            price=1000,
            currency="CZK",
            price_base=1000,
            status="PUBLISHED",
        )
        session.add(listing)
        session.flush()

        thread_id = str(uuid.uuid4())
        session.add(
            Thread(
                id=thread_id,
                listing_id=listing_id,
                channel="MARKETPLACE",
                counterparty="Scammer",
                lang="cs",
                status="OPEN",
            )
        )
        session.flush()

        msg_id = str(uuid.uuid4())
        suspicious_text = "Pošlete mi číslo karty a CVV kód pro převod peněz."
        msg = Message(
            id=msg_id,
            thread_id=thread_id,
            direction="IN",
            transport="MARKETPLACE",
            text_original=suspicious_text,
            lang_original="cs",
            source_evidence={},
        )
        session.add(msg)
        session.flush()

        # Process security
        res = process_incoming_message_security(session, msg_id, suspicious_text)
        session.commit()

        assert res.is_fraud_suspected is True
        assert res.scheme_id == "card_credentials"

        # Verify message source_evidence in DB
        msg_db = session.get(Message, msg_id)
        assert msg_db.escalated is True
        assert msg_db.source_evidence.get("fraud_suspected") is True
        assert msg_db.source_evidence.get("fraud_scheme") == "card_credentials"

        # Verify AuditEvent
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity_id == msg_id, AuditEvent.action == "FRAUD_SUSPECTED")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.data["can_auto_reply"] is False

        # Verify TruthLog
        truth = session.scalar(
            select(TruthLog)
            .where(TruthLog.claim.like(f"%{msg_id}%"))
            .order_by(TruthLog.at.desc())
        )
        assert truth is not None
        assert "card_credentials" in truth.claim

        # Cleanup
        session.query(Message).filter(Message.thread_id == thread_id).delete()
        session.query(Thread).filter(Thread.id == thread_id).delete()
        session.query(Listing).filter(Listing.id == listing_id).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()
