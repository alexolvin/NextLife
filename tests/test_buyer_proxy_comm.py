"""Tests for Buyer Proxy Communication without Binding Commitments (Task T-073 / Section I.3).

DoD:
- Contact with seller strictly without obligations or commitments.
- Report delivered to owner.
- Agent does not agree to price, does not reserve, does not promise prepayment.
- Comprehensive test across catalog of forbidden commitment constructions.
"""

import uuid
from decimal import Decimal

import pytest

from nextlife.services.buyer_communication import (
    FORBIDDEN_COMMITMENT_EXPRESSIONS,
    BindingCommitmentProhibitedError,
    render_non_binding_inquiry,
    send_seller_inquiry,
    validate_non_binding_inquiry,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BuyCandidate, BuyRequest, Marketplace, Outbox


def test_standard_inquiries_contain_zero_forbidden_commitments():
    # 1. Test CS & RU rendered inquiries
    questions = ["Je k dispozici originální balení?", "V jakém stavu jsou ventilátory?"]
    cs_inq = render_non_binding_inquiry(query="GeForce RTX 3070", questions=questions, lang="cs")
    ru_inq = render_non_binding_inquiry(query="GeForce RTX 3070", questions=questions, lang="ru")

    assert "nezávazným dotazem" in cs_inq
    assert "nepředstavuje závaznou rezervaci" in cs_inq
    assert "ознакомительный характер" in ru_inq

    # DoD test: verify that NONE of the forbidden commitment expressions appear in rendered inquiries
    for forbidden in FORBIDDEN_COMMITMENT_EXPRESSIONS:
        assert forbidden.lower() not in cs_inq.lower(), f"Forbidden phrase '{forbidden}' present in Czech inquiry!"
        assert forbidden.lower() not in ru_inq.lower(), f"Forbidden phrase '{forbidden}' present in Russian inquiry!"


def test_forbidden_commitments_validator_catches_all_forbidden_expressions():
    # Verify that trying to agree to a price, reserve, or promise prepayment is strictly caught
    for forbidden in FORBIDDEN_COMMITMENT_EXPRESSIONS:
        bad_message = f"Dobrý den, {forbidden}, pošlete podrobnosti."
        with pytest.raises(BindingCommitmentProhibitedError):
            validate_non_binding_inquiry(bad_message)


def test_send_seller_inquiry_and_owner_report():
    with get_db_session() as session:
        mp = session.get(Marketplace, "bazos_cz")
        if not mp:
            # Self-contained: create the marketplace this test needs so it does
            # not depend on cross-run seed state. The Marketplace schema is
            # title/country/currency/access_mode/enabled/config_hash (NOT the
            # old name/base_url/adapter_kind/is_active).
            mp = Marketplace(
                key="bazos_cz",
                title="Bazoš.cz",
                country="CZ",
                currency="CZK",
                access_mode="DIRECT",
                capabilities={},
                enabled=True,
                config_hash="0" * 64,
            )
            session.add(mp)
            session.flush()

        req = BuyRequest(
            id=str(uuid.uuid4()),
            query="Apple Mac mini M2",
            budget=Decimal("15000.00"),
            currency="CZK",
            attrs={"ram": "16GB"},
            status="ACTIVE",
        )
        session.add(req)
        session.flush()

        cand = BuyCandidate(
            id=str(uuid.uuid4()),
            request_id=req.id,
            marketplace_key="bazos_cz",
            external_url="https://bazos.cz/inzerat/mac-mini.php",
            price=Decimal("14500.00"),
            currency="CZK",
            price_base=Decimal("14500.00"),
            verdict="FAIR",
            contacted=False,
        )
        session.add(cand)
        session.commit()

        questions = [
            "Byl Mac mini kupován v ČR?",
            "Je součástí původní napájecí kabel a krabice?",
        ]

        try:
            res = send_seller_inquiry(
                session=session,
                candidate_id=cand.id,
                questions=questions,
                lang="cs",
                actor="BUYER_AGENT",
            )
            session.commit()

            assert res["contacted"] is True
            assert res["seller_outbox_id"] is not None
            assert res["owner_outbox_id"] is not None

            # Verify candidate marked contacted
            updated_cand = session.get(BuyCandidate, cand.id)
            assert updated_cand.contacted is True

            # Verify seller inquiry in outbox
            seller_outbox = session.get(Outbox, res["seller_outbox_id"])
            assert seller_outbox is not None
            assert "nezávazným dotazem" in seller_outbox.body
            assert "nepředstavuje závaznou rezervaci" in seller_outbox.body

            # Verify owner report in outbox
            owner_outbox = session.get(Outbox, res["owner_outbox_id"])
            assert owner_outbox is not None
            assert owner_outbox.recipient_hint == "OWNER"
            assert "Никаких обязательств по цене, бронированию или предоплате не дано" in owner_outbox.body
        finally:
            session.query(Outbox).filter(Outbox.recipient_hint.in_(["OWNER", "https://bazos.cz/inzerat/mac-mini.php"])).delete()
            session.query(BuyCandidate).filter(BuyCandidate.id == cand.id).delete()
            session.query(BuyRequest).filter(BuyRequest.id == req.id).delete()
            session.commit()
