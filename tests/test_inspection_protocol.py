"""Tests for Inspection Protocol (TZ v2.0 Section III.5.1 / Task T-063).

DoD:
- Checklist for GPU contains all nine points from TZ.
- Category without dedicated file gets general checklist from default.yaml.
- Checklists rendered for messages and UI.
- Outgoing filter blocks sensitive fields.
"""

import uuid
from decimal import Decimal

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.services.inspection import (
    enqueue_inspection_protocol_to_thread,
    load_inspection_checklist,
    render_inspection_message,
    render_inspection_ui_fragment,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing, Lot, Message, Outbox, Thread


def test_gpu_checklist_contains_all_nine_tz_points():
    checklist = load_inspection_checklist("gpu")
    points = checklist.get("points", [])
    assert len(points) == 9, f"GPU checklist must contain exactly 9 points, found {len(points)}"

    # Check for core TZ inspection concepts
    points_ru = [p["ru"].lower() for p in points]

    assert any("серийный номер" in p for p in points_ru), "Missing serial number check"
    assert any("следы вскрытия" in p and "пломб" in p for p in points_ru), "Missing visual / seal check"
    assert any("вентилятор" in p and "30/70/100%" in p for p in points_ru), "Missing fan speed check"
    assert any("стенд" in p for p in points_ru), "Missing test bench check"
    assert any("15 минут" in p and "температур" in p for p in points_ru), "Missing 15 min stress test"
    assert any("артефакт" in p and "3d" in p for p in points_ru), "Missing 3D artifact check"
    assert any("видеовыход" in p for p in points_ru), "Missing video outputs check"
    assert any("разъём" in p and "питания" in p for p in points_ru), "Missing power connector check"
    assert any("комплект" in p for p in points_ru), "Missing package/bundle check"


def test_category_without_file_falls_back_to_default():
    default_checklist = load_inspection_checklist("unknown_random_category_123")
    assert default_checklist["category"] == "default"
    assert len(default_checklist["points"]) >= 5

    none_checklist = load_inspection_checklist(None)
    assert none_checklist["category"] == "default"


def test_category_aliases_resolve_to_gpu():
    for alias in ["gpu", "GPU", "graphics_card", "videocard", "видеокарта", "Grafická karta"]:
        chk = load_inspection_checklist(alias)
        assert chk["category"] == "gpu"
        assert len(chk["points"]) == 9


def test_render_inspection_message_cs_and_ru():
    cs_msg = render_inspection_message("gpu", lang="cs", extra_notes="Test bench available")
    assert "Protokol o převzetí a kontrole" in cs_msg
    assert "Grafická karta" in cs_msg
    assert "Test bench available" in cs_msg
    assert "1." in cs_msg
    assert "9." in cs_msg

    ru_msg = render_inspection_message("gpu", lang="ru")
    assert "Протокол проверки и приёма-передачи" in ru_msg
    assert "Видеокарта" in ru_msg
    assert "15 минут" in ru_msg


def test_inspection_message_blocks_internal_fields():
    with pytest.raises(InternalFieldLeakageError):
        # Passing internal field in extra notes or context that triggers filter
        render_inspection_message("gpu", lang="ru", extra_notes="Ниже price_floor: 2000 не отдавать")


def test_render_inspection_ui_fragment():
    html_fragment = render_inspection_ui_fragment("gpu", lang="ru", show_secondary=True)
    assert 'class="protocol"' in html_fragment
    assert 'id="inspection-protocol"' in html_fragment
    assert "Видеокарта" in html_fragment
    assert 'data-point-id="serial_check"' in html_fragment
    assert 'data-point-id="stress_test_15min"' in html_fragment


def test_enqueue_inspection_protocol_to_thread():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="GPU RTX 3080 Test Item",
            category_key="general",
            price=Decimal("12000.00"),
        )
        lot_code = created["lot_code"]

        listing_id = str(uuid.uuid4())
        listing = Listing(
            id=listing_id,
            item_code=created["item_code"],
            marketplace_key="bazos_cz",
            price=Decimal("12000.00"),
            currency="CZK",
            price_base=Decimal("12000.00"),
            status="PUBLISHED",
        )
        session.add(listing)
        session.flush()

        thread = Thread(
            id=str(uuid.uuid4()),
            listing_id=listing_id,
            channel="EMAIL",
            counterparty="buyer_gpu@example.com",
            lang="cs",
            status="ACTIVE",
        )
        session.add(thread)
        session.commit()

        try:
            msg, outbox_msg = enqueue_inspection_protocol_to_thread(
                session=session,
                thread_id=thread.id,
                category="gpu",
                lang="cs",
                actor="TEST_ACTOR",
            )
            session.commit()

            assert msg.id is not None
            assert msg.direction == "OUT"
            assert msg.template_key == "inspection_protocol"
            assert "Protokol o převzetí a kontrole" in msg.text_original

            assert outbox_msg.id is not None
            assert outbox_msg.state == "PENDING"
            assert outbox_msg.recipient_hint == "buyer_gpu@example.com"
            assert "Grafická karta" in outbox_msg.body

            # Check database persistence
            saved_msg = session.get(Message, msg.id)
            assert saved_msg is not None
            assert saved_msg.direction == "OUT"

            saved_outbox = session.get(Outbox, outbox_msg.id)
            assert saved_outbox is not None
        finally:
            # Clean up
            session.query(Outbox).filter(Outbox.recipient_hint == "buyer_gpu@example.com").delete()
            session.query(Message).filter(Message.thread_id == thread.id).delete()
            session.query(Thread).filter(Thread.id == thread.id).delete()
            session.query(Listing).filter(Listing.id == listing_id).delete()
            session.query(Lot).filter(Lot.code == lot_code).delete()
            session.query(Item).filter(Item.code == created["item_code"]).delete()
            session.commit()
