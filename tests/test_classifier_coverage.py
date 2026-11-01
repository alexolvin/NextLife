"""Tests for Message Classification and Card Data Full-Coverage Verification (T-054, Section I.2 Ш10).

DoD:
- Auto-reply is sent ONLY when all required characteristics/fields exist in item card.
- A question about a characteristic missing from the card strictly NEVER receives an auto-reply under any phrasing.
- Fraud suspected messages never receive an auto-reply.
- Price bargaining always escalates.
- Module length <= 400 lines.
"""

from decimal import Decimal

from sqlalchemy import select

from nextlife.comms.classifier import process_incoming_message
from nextlife.core.config import load_config
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Lot


def _create_item(session, **kwargs):
    created = create_item_and_lot(
        session=session,
        title="Graphics Card RTX 3080",
        category_key="electronics",
        price=Decimal("12000.00"),
    )
    lot_code = created["lot_code"]
    item_code = created["item_code"]

    item = session.get(Item, item_code)
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    item.status = "PUBLISHED"
    lot.status = "PUBLISHED"

    for k, v in kwargs.items():
        setattr(item, k, v)
    session.flush()
    return item_code, lot_code


def test_missing_mining_history_never_auto_replies_across_any_phrasing():
    """DoD: Question about missing characteristic strictly does not auto-reply under any phrasing."""
    with get_db_session() as session:
        # usage_profile is NOT set (None)
        item_code, lot_code = _create_item(session, usage_profile=None)
        cfg = load_config()

        phrasings = [
            "Byla karta těžena?",
            "Těžilo se na ní?",
            "Byla těžena na těžbu kryptoměn?",
            "Použito na těžbu?",
            "Tezilo se s tim v rigu?",
            "Was it mined on?",
        ]

        for phrase in phrasings:
            res = process_incoming_message(
                session=session,
                sender="buyer@test.cz",
                subject=f"Dotaz na kartu [NL-{item_code}]",
                body=phrase,
                config=cfg,
            )
            # Must strictly escalate, NEVER auto-reply
            assert res["type"] == "ESCALATION", f"Failed for phrasing: {phrase}"
            assert res.get("can_auto_reply") is False, f"Auto-reply allowed for: {phrase}"
            assert res["requires_owner_approval"] is True

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_present_mining_history_produces_verified_auto_reply():
    with get_db_session() as session:
        # Card explicitly has usage_profile confirmed
        item_code, lot_code = _create_item(session, usage_profile="pouze občasné hraní o víkendech")
        cfg = load_config()

        res = process_incoming_message(
            session=session,
            sender="buyer@test.cz",
            subject=f"Dotaz na kartu [NL-{item_code}]",
            body="Dobrý den, byla karta těžena?",
            config=cfg,
        )

        assert res["type"] == "AUTO_REPLY"
        assert "pouze občasné hraní o víkendech" in res["response_text"]
        assert res["source_field"] == "usage_profile"

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_missing_warranty_or_receipt_escalates():
    with get_db_session() as session:
        # warranty and receipt are missing (None)
        item_code, lot_code = _create_item(
            session, manufacturer_warranty_until=None, receipt_present=None
        )
        cfg = load_config()

        warranty_queries = [
            "Je to ještě v záruce?",
            "Platí ještě záruka výrobce?",
            "Má to záruku?",
            "Is it under warranty?",
        ]
        for q in warranty_queries:
            res = process_incoming_message(
                session=session,
                sender="buyer@test.cz",
                subject=f"Dotaz [NL-{item_code}]",
                body=q,
                config=cfg,
            )
            assert res["type"] == "ESCALATION"
            assert "manufacturer_warranty_until" in res.get("missing_fields", [])

        receipt_queries = [
            "Máte fakturu?",
            "Je k tomu účtenka?",
            "Máte doklad o koupi?",
        ]
        for q in receipt_queries:
            res = process_incoming_message(
                session=session,
                sender="buyer@test.cz",
                subject=f"Dotaz [NL-{item_code}]",
                body=q,
                config=cfg,
            )
            assert res["type"] == "ESCALATION"
            assert "receipt_present" in res.get("missing_fields", [])

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_missing_dimensions_or_weight_escalates():
    with get_db_session() as session:
        item_code, lot_code = _create_item(
            session, dim_l_mm=None, dim_w_mm=None, dim_h_mm=None, weight_g=None
        )
        cfg = load_config()

        dim_res = process_incoming_message(
            session=session,
            sender="buyer@test.cz",
            subject=f"Dotaz [NL-{item_code}]",
            body="Jaké jsou přesné rozměry v cm?",
            config=cfg,
        )
        assert dim_res["type"] == "ESCALATION"
        assert "dim_l_mm" in dim_res.get("missing_fields", [])

        weight_res = process_incoming_message(
            session=session,
            sender="buyer@test.cz",
            subject=f"Dotaz [NL-{item_code}]",
            body="Kolik to váží v gramech?",
            config=cfg,
        )
        assert weight_res["type"] == "ESCALATION"
        assert "weight_g" in weight_res.get("missing_fields", [])

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_price_negotiation_never_auto_replies():
    with get_db_session() as session:
        item_code, lot_code = _create_item(session)
        cfg = load_config()

        res = process_incoming_message(
            session=session,
            sender="bargainer@test.cz",
            subject=f"Dotaz [NL-{item_code}]",
            body="Dobrý den, dáte to za 11 000 Kč?",
            config=cfg,
        )
        assert res["type"] == "ESCALATION"
        assert res["requires_owner_approval"] is True

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_fraud_attempt_escalates_and_refuses_auto_reply():
    with get_db_session() as session:
        item_code, lot_code = _create_item(session)
        cfg = load_config()

        res = process_incoming_message(
            session=session,
            sender="scammer@test.cz",
            subject=f"Dotaz [NL-{item_code}]",
            body="Dobrý den, zde je odkaz na platbu: https://dpd-platba.cz/order/123",
            config=cfg,
        )
        assert res["type"] == "ESCALATION"
        assert res["reason"] == "fraud_suspected"
        assert res["can_auto_reply"] is False

        # Cleanup
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()
