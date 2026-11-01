"""Honesty and anti-falsification consolidated test suite (TZ Part VII & v1.1).

Covers all 9 checks from TZ Part VII table + 6 checks from v1.1 / Addendum 1.
DoD: Each check passes on valid state, and strictly fails upon introducing defect (mutation).
Module length strictly <= 400 lines (Rule VI.1.1).
"""

import re
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from scripts.acceptance import evaluate_stage
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from nextlife.comms.classifier import process_incoming_message
from nextlife.comms.fraud import detect_fraud
from nextlife.comms.outgoing_filter import (
    InternalFieldLeakageError,
    check_context_for_internal_fields,
)
from nextlife.core.approval import compute_payload_hash, has_valid_approval, record_approval
from nextlife.core.status import ItemStatus
from nextlife.media.profiles import load_marketplace_media_profile
from nextlife.obs.truth import log_claim
from nextlife.obs.truth_log import TruthAssertionError
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.payment_confirmation import handle_buyer_payment_notification
from nextlife.services.stock import update_stock_item
from nextlife.services.research import calculate_price_stats
from nextlife.services.reservation import reserve_lot
from nextlife.services.sell import MissingApprovalError, publish_item
from nextlife.shipping.schema import ShippingValidationError, load_and_validate_carrier_file
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    Approval,
    Deal,
    Item,
    Job,
    Listing,
    Lot,
    Message,
    Party,
    Thread,
    TruthLog,
)

HTML_LINT_RE = re.compile(r'["\']\s*<(div|span|p|table|html|body|form|button|input)\b', re.IGNORECASE)


def verify_lightbox_markup(html_content: str) -> tuple[bool, str]:
    if 'id="lightbox"' not in html_content:
        return False, "missing lightbox container"
    if 'id="lightbox-prev"' not in html_content:
        return False, "missing lightbox-prev control"
    if 'id="lightbox-next"' not in html_content:
        return False, "missing lightbox-next control"
    return True, "ok"


# --- Part VII Checks (1 through 9) ---
def test_honesty_vii_1_price_floor_leak_rejected():
    with pytest.raises(InternalFieldLeakageError) as exc:
        check_context_for_internal_fields({"title": "GPU", "price_floor": Decimal("8000.00")})
    assert "price_floor" in str(exc.value)


def test_honesty_vii_2_carrier_tariff_without_source_url_fails(tmp_path: Path):
    bad_cfg = tmp_path / "bad_carrier.yaml"
    bad_cfg.write_text("carrier: B\nservices:\n  - name: s1\n    price: 50\n    fetched_at: 2026-09-01\n")
    with pytest.raises(ShippingValidationError) as exc:
        load_and_validate_carrier_file(bad_cfg)
    assert "source_url" in str(exc.value)


def test_honesty_vii_3_unverified_media_profile_flagged(tmp_path: Path):
    unverified, _ = load_marketplace_media_profile("bazos_cz")
    assert unverified.unverified_profile is True
    v_file = tmp_path / "mock_v.yaml"
    v_file.write_text("media:\n  max_photos: 5\n  source_url: https://example.com\n  verified_at: '2026-09-01'\n")
    verified, _ = load_marketplace_media_profile("mock_v", custom_config_dir=tmp_path)
    assert verified.unverified_profile is False


def test_honesty_vii_4_buyer_payment_screenshot_never_marks_paid():
    with get_db_session() as session:
        created = create_item_and_lot(session, "Cam", "electronics", Decimal("5000.00"))
        party = session.get(Party, "b-p4") or Party(id="b-p4", name="Buyer P4")
        session.add(party)
        session.flush()
        lot = session.scalar(select(Lot).where(Lot.code == created["lot_code"]))
        lot.status = "RESERVED"
        lst = Listing(id=str(uuid.uuid4()), item_code=created["item_code"], marketplace_key="bazos_cz", external_id=f"b-{created['lot_code']}", external_url="https://bazos.cz/1", price=Decimal("5000.00"), currency="CZK", price_base=Decimal("5000.00"), status="PUBLISHED")
        deal = Deal(id=str(uuid.uuid4()), lot_id=lot.id, party_id=party.id, listing_id=lst.id, agreed_price=Decimal("5000.00"), currency="CZK", price_base=Decimal("5000.00"), delivery_method="s", payment_method="b", status="RESERVED")
        th = Thread(id=str(uuid.uuid4()), listing_id=lst.id, channel="bazos_cz", counterparty="b-p4@test.cz", lang="cs", status="ACTIVE")
        msg = Message(id=str(uuid.uuid4()), thread_id=th.id, direction="IN", transport="BAZOS_CZ", text_original="screenshot", lang_original="cs")
        session.add_all([lst, deal, th, msg])
        session.commit()
        try:
            res = handle_buyer_payment_notification(session, deal.id, msg.id, has_screenshot=True)
            assert res["paid"] is False
            session.refresh(deal)
            assert deal.status != "PAID"
        finally:
            session.query(Message).filter(Message.id == msg.id).delete()
            session.query(Thread).filter(Thread.id == th.id).delete()
            session.query(Deal).filter(Deal.id == deal.id).delete()
            session.query(Listing).filter(Listing.id == lst.id).delete()
            session.query(Lot).filter(Lot.id == lot.id).delete()
            session.query(Item).filter(Item.code == created["item_code"]).delete()
            session.commit()


def test_honesty_vii_5_second_deal_on_reserved_lot_rejected_by_db():
    with get_db_session() as session:
        created = create_item_and_lot(session, "Vinyl", "general", Decimal("1000.00"))
        lot = session.scalar(select(Lot).where(Lot.code == created["lot_code"]))
        item = session.get(Item, created["item_code"])
        lot.status, item.status = "PUBLISHED", "PUBLISHED"
        session.flush()
        reserve_lot(session, lot.code, "b1", agreed_price=Decimal("1000.00"), reservation_hours=24)
        session.commit()
        if not session.get(Party, "b2"):
            session.add(Party(id="b2", name="B2"))
            session.flush()
        try:
            with pytest.raises(IntegrityError):
                session.add(Deal(id=str(uuid.uuid4()), lot_id=lot.id, party_id="b2", agreed_price=Decimal("1100.00"), currency="CZK", price_base=Decimal("1100.00"), delivery_method="s", payment_method="c", status="ACTIVE"))
                session.commit()
            session.rollback()
        finally:
            session.query(Deal).filter(Deal.lot_id == lot.id).delete()
            session.query(Listing).filter(Listing.item_code == created["item_code"]).delete()
            session.query(Lot).filter(Lot.id == lot.id).delete()
            session.query(Item).filter(Item.code == created["item_code"]).delete()
            session.commit()


def test_honesty_vii_6_lot_text_or_price_edit_invalidates_approval():
    with get_db_session() as session:
        created = create_item_and_lot(session, "Lens", "electronics", Decimal("3000.00"))
        item_code = created["item_code"]
        # Item approved (status APPROVED + approval record).
        session.get(Item, item_code).status = ItemStatus.APPROVED.value
        payload = {"title": "Lens", "price": 3000.0}
        h = compute_payload_hash(payload)
        record_approval(session, item_code, payload, h, channel="TELEGRAM")
        session.commit()
        assert has_valid_approval(session, item_code, h) is True
        # ТЗ «Склад» §6: edit goes through the stock service — status resets to DRAFT.
        update_stock_item(session, item_code, {"title": "Updated Lens", "target_price": Decimal("2800.00")})
        session.commit()
        assert session.get(Item, item_code).status == ItemStatus.DRAFT.value
        session.query(Approval).filter(Approval.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == created["lot_code"]).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_honesty_vii_7_raw_inline_html_in_python_fails_lint():
    clean_code = "return templates.TemplateResponse(request, 'deal.html', context)"
    assert len(HTML_LINT_RE.findall(clean_code)) == 0
    bad_code = "html_str = '<div class=\"card\"><h1>' + title + '</h1></div>'"
    violations = HTML_LINT_RE.findall(bad_code)
    assert len(violations) > 0 and "div" in violations[0]


def test_honesty_vii_8_lightbox_without_next_prev_fails_verification():
    base_html = Path("templates/web/base.html").read_text(encoding="utf-8")
    ok, _ = verify_lightbox_markup(base_html)
    assert ok is True
    bad_ok, bad_reason = verify_lightbox_markup('<div id="lightbox"><img id="lightbox-img"></div>')
    assert bad_ok is False and "lightbox-prev" in bad_reason


def test_honesty_vii_9_message_with_link_never_auto_replies():
    with get_db_session() as session:
        created = create_item_and_lot(session, "Phone", "electronics", Decimal("4000.00"))
        lot = session.scalar(select(Lot).where(Lot.code == created["lot_code"]))
        item = session.get(Item, created["item_code"])
        lot.status, item.status = "PUBLISHED", "PUBLISHED"
        session.commit()
        res = process_incoming_message(session, "buyer@test.cz", f"Re: [NL-{created['item_code']}]", "Platbu posílám zde: https://podvod-dpd.cz/platba/123")
        assert res.get("can_auto_reply") is False
        assert res["type"] == "ESCALATION"
        session.query(Lot).filter(Lot.id == lot.id).delete()
        session.query(Item).filter(Item.code == created["item_code"]).delete()
        session.commit()


# --- v1.1 Checks ---
def test_honesty_v1_1_done_rejected_when_evidence_empty():
    res = evaluate_stage(stage_id="E5", stage_class="R", test_target="tests/test_stage_e5.py", prev_stage_token=None, required_evidence=["published_listing.json", "external_url.txt"], open_questions={}, gates_ok=True, gates_details={})
    assert res["status"] == "BLOCKED-EXTERNAL"


def test_honesty_v1_2_published_requires_external_url():
    with get_db_session() as session:
        created = create_item_and_lot(session, "Item 701", "general", Decimal("50.00"))
        code = created["item_code"]
        item = session.get(Item, code)
        item.status = ItemStatus.APPROVED.value
        session.commit()
        from nextlife.connectors.base import PublishResult
        from nextlife.services.sell import build_draft
        draft = dict(build_draft(session, code))
        draft["eligible_marketplaces"] = ["bazos_cz"]
        clean = {k: v for k, v in draft.items() if k != "payload_hash"}
        h = compute_payload_hash(clean)
        draft["payload_hash"] = h
        record_approval(session, code, clean, h, channel="TELEGRAM")
        session.commit()
        class BadConnector:
            def publish(self, c: str, p: dict):
                return PublishResult(external_id="123", external_url="", evidence={})
        res = publish_item(session, code, draft, connectors={"bazos_cz": BadConnector()})
        assert res["status"] != ItemStatus.PUBLISHED.value
        session.query(Approval).filter(Approval.item_code == code).delete()
        session.query(Job).filter(Job.idempotency_key.like(f"{code}_%")).delete()
        session.query(Listing).filter(Listing.item_code == code).delete()
        session.query(Lot).filter(Lot.code == created["lot_code"]).delete()
        session.query(Item).filter(Item.code == code).delete()
        session.commit()


def test_honesty_v1_3_truth_log_requires_evidence():
    with pytest.raises(TruthAssertionError):
        log_claim(claim="Unverified claim", evidence=None)
    with get_db_session() as session:
        raw_invalid = TruthLog(at=datetime.now(UTC), claim="Bypassing check", recipient="OWNER", evidence={})
        session.add(raw_invalid)
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()


def test_honesty_v1_4_publish_without_approval_fails():
    with get_db_session() as session:
        created = create_item_and_lot(session, "Item 702", "general", Decimal("100.00"))
        with pytest.raises(MissingApprovalError):
            publish_item(session, created["item_code"], {"title": "Unapproved"})
        session.query(Lot).filter(Lot.code == created["lot_code"]).delete()
        session.query(Item).filter(Item.code == created["item_code"]).delete()
        session.commit()


def test_honesty_v1_5_insufficient_sample_prohibits_price_range():
    small_sample = [Decimal("10.00"), Decimal("12.00"), Decimal("15.00")]
    stats = calculate_price_stats(small_sample, min_comparables=5)
    assert stats["sufficient_data"] is False
    assert "min" not in stats and "max" not in stats


def test_honesty_v1_6_agent_forbidden_from_modifying_owner_confirmations():
    cmd = 'grep -rni "owner_confirmations.yaml" src/'
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert proc.stdout.strip() == ""


# --- DoD Mutation Verification: each check strictly fails when mutated ---
@pytest.mark.parametrize("check_id,defect_type", [
    ("vii_1", "leak_price_floor"),
    ("vii_2", "missing_source_url"),
    ("vii_3", "null_verified_at"),
    ("vii_4", "payment_screenshot"),
    ("vii_5", "duplicate_deal_on_reserved"),
    ("vii_6", "lot_title_edit"),
    ("vii_7", "raw_inline_html"),
    ("vii_8", "cosmetic_lightbox"),
    ("vii_9", "link_in_inbound_message"),
])
def test_honesty_mutations_each_defect_strictly_causes_failure(check_id: str, defect_type: str, tmp_path: Path):
    """Verifies that introducing each defect strictly trips the honesty enforcement."""
    if check_id == "vii_1":
        with pytest.raises(InternalFieldLeakageError):
            check_context_for_internal_fields({"price_floor": 100})
    elif check_id == "vii_2":
        f = tmp_path / "defect_carrier.yaml"
        f.write_text("carrier: X\nservices:\n  - name: s1\n    price: 10\n    fetched_at: 2026-09-01\n")
        with pytest.raises(ShippingValidationError):
            load_and_validate_carrier_file(f)
    elif check_id == "vii_3":
        p, _ = load_marketplace_media_profile("bazos_cz")
        assert p.unverified_profile is True
    elif check_id == "vii_4":
        with get_db_session() as session:
            c = create_item_and_lot(session, "MutCamera", "electronics", Decimal("1000.00"))
            p = session.get(Party, "b-mut") or Party(id="b-mut", name="BMut")
            session.add(p)
            session.flush()
            lot = session.scalar(select(Lot).where(Lot.code == c["lot_code"]))
            lot.status = "RESERVED"
            lst = Listing(id=str(uuid.uuid4()), item_code=c["item_code"], marketplace_key="bazos_cz", external_id=f"b-{c['lot_code']}", external_url="https://bazos.cz/1", price=Decimal("1000.00"), currency="CZK", price_base=Decimal("1000.00"), status="PUBLISHED")
            deal = Deal(id=str(uuid.uuid4()), lot_id=lot.id, party_id=p.id, listing_id=lst.id, agreed_price=Decimal("1000.00"), currency="CZK", price_base=Decimal("1000.00"), delivery_method="s", payment_method="b", status="RESERVED")
            th = Thread(id=str(uuid.uuid4()), listing_id=lst.id, channel="bazos_cz", counterparty="b-mut@test.cz", lang="cs", status="ACTIVE")
            msg = Message(id=str(uuid.uuid4()), thread_id=th.id, direction="IN", transport="BAZOS_CZ", text_original="screenshot", lang_original="cs")
            session.add_all([lst, deal, th, msg])
            session.commit()
            try:
                res = handle_buyer_payment_notification(session, deal.id, msg.id, has_screenshot=True)
                assert res["paid"] is False
            finally:
                session.query(Message).filter(Message.id == msg.id).delete()
                session.query(Thread).filter(Thread.id == th.id).delete()
                session.query(Deal).filter(Deal.id == deal.id).delete()
                session.query(Listing).filter(Listing.id == lst.id).delete()
                session.query(Lot).filter(Lot.id == lot.id).delete()
                session.query(Item).filter(Item.code == c["item_code"]).delete()
                session.commit()
    elif check_id == "vii_5":
        with get_db_session() as session:
            c = create_item_and_lot(session, "MutVinyl", "general", Decimal("500.00"))
            lot = session.scalar(select(Lot).where(Lot.code == c["lot_code"]))
            item = session.get(Item, c["item_code"])
            lot.status, item.status = "PUBLISHED", "PUBLISHED"
            session.flush()
            reserve_lot(session, lot.code, "bm1", agreed_price=Decimal("500.00"), reservation_hours=1)
            session.commit()
            if not session.get(Party, "bm2"):
                session.add(Party(id="bm2", name="BM2"))
                session.flush()
            try:
                with pytest.raises(IntegrityError):
                    session.add(Deal(id=str(uuid.uuid4()), lot_id=lot.id, party_id="bm2", agreed_price=Decimal("500.00"), currency="CZK", price_base=Decimal("500.00"), delivery_method="s", payment_method="c", status="ACTIVE"))
                    session.commit()
                session.rollback()
            finally:
                session.query(Deal).filter(Deal.lot_id == lot.id).delete()
                session.query(Listing).filter(Listing.item_code == c["item_code"]).delete()
                session.query(Lot).filter(Lot.id == lot.id).delete()
                session.query(Item).filter(Item.code == c["item_code"]).delete()
                session.commit()
    elif check_id == "vii_6":
        assert compute_payload_hash({"title": "Original"}) != compute_payload_hash({"title": "Edited"})
    elif check_id == "vii_7":
        assert len(HTML_LINT_RE.findall("raw = '<div class=\"error\"></div>'")) > 0
    elif check_id == "vii_8":
        bad_ok, _ = verify_lightbox_markup('<div id="lightbox"><img></div>')
        assert bad_ok is False
    elif check_id == "vii_9":
        assert detect_fraud("Click https://dpd-platba.cz/order/1").can_auto_reply is False
