"""Tests for Buyer Checklist & Seller Inquiry (Task T-072 / Section III.5.1).

DoD:
- Generates questions for the seller and on-site inspection checklist.
- For GPU: produces the exact same 9 inspection protocol points as during sale.
- Verified by outgoing filter.
"""

import uuid
from decimal import Decimal

from nextlife.services.buyer_checklist import (
    generate_buyer_dossier,
    get_buyer_inspection_protocol,
    get_seller_questions,
    render_buyer_inquiry_message,
)
from nextlife.services.inspection import get_checklist_items
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BuyCandidate, BuyRequest, Marketplace


def test_gpu_checklist_identical_to_seller_inspection_protocol():
    # DoD: для видеокарты выдаётся тот же протокол, что и при продаже.
    buyer_gpu_points = get_buyer_inspection_protocol("gpu", lang="ru")
    seller_gpu_points = get_checklist_items("gpu", lang="ru")

    assert len(buyer_gpu_points) == 9, f"Buyer GPU protocol must have 9 points, got {len(buyer_gpu_points)}"
    assert len(seller_gpu_points) == 9

    # Compare each point
    for b_pt, s_pt in zip(buyer_gpu_points, seller_gpu_points, strict=True):
        assert b_pt["id"] == s_pt["id"]
        assert b_pt["text"] == s_pt["text"]

    # Verify key points
    texts = [p["text"].lower() for p in buyer_gpu_points]
    assert any("серийный номер" in t for t in texts)
    assert any("30/70/100%" in t for t in texts)
    assert any("15 минут" in t for t in texts)
    assert any("артефакт" in t for t in texts)


def test_seller_questions_by_category():
    gpu_questions_cs = get_seller_questions("gpu", lang="cs")
    assert len(gpu_questions_cs) >= 4
    assert any("mining" in q.lower() or "těžbě" in q.lower() for q in gpu_questions_cs)
    assert any("plomby" in q.lower() for q in gpu_questions_cs)
    assert any("coil whine" in q.lower() or "ventilátory" in q.lower() for q in gpu_questions_cs)

    gpu_questions_ru = get_seller_questions("gpu", lang="ru")
    assert any("майнинг" in q.lower() for q in gpu_questions_ru)
    assert any("пломб" in q.lower() for q in gpu_questions_ru)

    default_questions = get_seller_questions("unknown_cat", lang="cs")
    assert len(default_questions) >= 3
    assert any("funkční" in q.lower() for q in default_questions)


def test_render_buyer_inquiry_message():
    msg_cs = render_buyer_inquiry_message(query="RTX 3080", category="gpu", lang="cs")
    assert "Dobrý den" in msg_cs
    assert "[RTX 3080]" in msg_cs
    assert "mining" in msg_cs.lower() or "těžbě" in msg_cs.lower()
    assert "Předem děkuji" in msg_cs

    msg_ru = render_buyer_inquiry_message(query="RTX 3080", category="gpu", lang="ru")
    assert "Здравствуйте" in msg_ru
    assert "майнинг" in msg_ru.lower()


def test_generate_buyer_dossier():
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
            query="RTX 4080 Gaming",
            budget=Decimal("25000.00"),
            currency="CZK",
            attrs={"category": "gpu"},
            status="ACTIVE",
        )
        session.add(req)
        session.flush()

        cand = BuyCandidate(
            id=str(uuid.uuid4()),
            request_id=req.id,
            marketplace_key="bazos_cz",
            external_url="https://bazos.cz/item/4080",
            price=Decimal("24000.00"),
            currency="CZK",
            price_base=Decimal("24000.00"),
            verdict="FAIR",
            contacted=False,
        )
        session.add(cand)
        session.commit()

        try:
            dossier = generate_buyer_dossier(cand.id, session=session, lang="cs")
            assert dossier["candidate_id"] == cand.id
            assert dossier["category"] == "gpu"
            assert dossier["inspection_protocol_count"] == 9, "Must contain all 9 GPU inspection points"
            assert dossier["questions_count"] >= 4
            assert "Dobrý den" in dossier["inquiry_message"]
        finally:
            session.query(BuyCandidate).filter(BuyCandidate.id == cand.id).delete()
            session.query(BuyRequest).filter(BuyRequest.id == req.id).delete()
            session.commit()
