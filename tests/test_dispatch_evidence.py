"""Tests for Deal Dispatch Evidence & Completeness (TZ v2.0 Section III.6 / Task T-066).

DoD:
- Four mandatory dispatch evidences:
  1. box_content_photo
  2. serial_number_photo
  3. sealed_box_label_photo
  4. tracking_and_weight_receipt
- Verification of evidence completeness.
- Dispatch allowed without full set, but flagged as evidence_incomplete.
- Insurance threshold enforcement.
- API endpoints for uploading and querying evidence.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.services.dispatch_evidence import (
    dispatch_deal,
    get_evidence_status,
    submit_dispatch_evidence,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Handover, Item, Lot, Party


@pytest.fixture
def test_deal():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Dispatch Test Console",
            category_key="electronics",
            price=Decimal("6500.00"),
        )
        lot = session.query(Lot).filter(Lot.code == created["lot_code"]).first()
        party = Party(
            id=str(uuid.uuid4()),
            name="Tomas Carrier Buyer",
            email="tomas.buyer@example.cz",
        )
        session.add(party)

        deal = Deal(
            id=str(uuid.uuid4()),
            lot_id=lot.id,
            party_id=party.id,
            agreed_price=Decimal("6500.00"),
            currency="CZK",
            price_base=Decimal("6500.00"),
            delivery_method="ZASILKOVNA",
            payment_method="BANK_TRANSFER",
            status="PAID",
        )
        session.add(deal)
        session.commit()

        deal_id = deal.id
        lot_code = lot.code
        item_code = created["item_code"]
        party_id = party.id

    yield deal_id

    # Cleanup
    with get_db_session() as session:
        session.query(Handover).filter(Handover.deal_id == deal_id).delete()
        session.query(Deal).filter(Deal.id == deal_id).delete()
        session.query(Party).filter(Party.id == party_id).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_evidence_completeness_flow(test_deal):
    deal_id = test_deal
    with get_db_session() as session:
        # Initial status: 0 evidences
        status_0 = get_evidence_status(session, deal_id)
        assert not status_0["is_complete"]
        assert status_0["evidence_incomplete"]
        assert len(status_0["missing"]) == 4

        # Submit 1st evidence
        res1 = submit_dispatch_evidence(
            session, deal_id, "box_content_photo", "data/media/box_inside.jpg"
        )
        assert not res1["is_complete"]
        assert res1["present_count"] == 1
        assert "box_content_photo" not in res1["missing"]

        # Submit 2nd evidence
        res2 = submit_dispatch_evidence(
            session, deal_id, "serial_number_photo", "data/media/sn_macro.jpg"
        )
        assert not res2["is_complete"]

        # Submit 3rd evidence
        res3 = submit_dispatch_evidence(
            session, deal_id, "sealed_box_label_photo", "data/media/sealed_label.jpg"
        )
        assert not res3["is_complete"]
        assert len(res3["missing"]) == 1
        assert res3["missing"] == ["tracking_and_weight_receipt"]

        # Submit 4th evidence (complete!)
        res4 = submit_dispatch_evidence(
            session, deal_id, "tracking_and_weight_receipt", "data/media/receipt_weight.pdf"
        )
        assert res4["is_complete"]
        assert len(res4["missing"]) == 0

        # Dispatch deal with complete evidence
        dispatch_res = dispatch_deal(
            session=session,
            deal_id=deal_id,
            tracking_number="Z1234567890",
            carrier="Zásilkovna",
        )
        assert not dispatch_res["evidence_incomplete"]
        assert dispatch_res["handover_status"] == "DISPATCHED"
        assert dispatch_res["requires_insurance"] is True  # 6500 CZK >= 5000 threshold


def test_dispatch_with_incomplete_evidence_flagged(test_deal):
    deal_id = test_deal
    with get_db_session() as session:
        # Submit only 1 evidence out of 4
        submit_dispatch_evidence(
            session, deal_id, "box_content_photo", "data/media/box_inside.jpg"
        )

        # Dispatch anyway
        dispatch_res = dispatch_deal(
            session=session,
            deal_id=deal_id,
            tracking_number="Z999888777",
            carrier="Zásilkovna",
        )

        assert dispatch_res["evidence_incomplete"] is True, "Must be flagged evidence_incomplete"
        assert len(dispatch_res["missing_evidences"]) == 3
        assert dispatch_res["handover_status"] == "DISPATCHED"

        # Check database record
        handover = session.query(Handover).filter(Handover.deal_id == deal_id).first()
        assert handover is not None
        assert handover.evidence["evidence_incomplete"] is True


def test_evidence_api_endpoints(test_deal):
    app = create_app()
    client = TestClient(app)
    deal_id = test_deal

    from nextlife.core.secrets import resolve_secret
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET evidence status
    resp_get = client.get(f"/api/deals/{deal_id}/evidence", headers=headers)
    assert resp_get.status_code == 200
    data_get = resp_get.json()
    assert data_get["deal_id"] == deal_id
    assert data_get["evidence_incomplete"] is True

    # 2. POST evidence
    resp_post = client.post(
        f"/api/deals/{deal_id}/evidence",
        json={
            "evidence_type": "box_content_photo",
            "file_path": "/path/to/photo.jpg",
            "metadata": {"source": "camera_upload"},
        },
        headers=headers,
    )
    assert resp_post.status_code == 200
    data_post = resp_post.json()
    assert data_post["evidence_type"] == "box_content_photo"
    assert data_post["present_count"] == 1

    # 3. POST dispatch
    resp_disp = client.post(
        f"/api/deals/{deal_id}/dispatch",
        json={
            "tracking_number": "TRK-98765",
            "carrier": "Zásilkovna",
        },
        headers=headers,
    )
    assert resp_disp.status_code == 200
    data_disp = resp_disp.json()
    assert data_disp["evidence_incomplete"] is True
    assert data_disp["handover_status"] == "DISPATCHED"
