"""Tests for Meeting Places and Safety Enforcement (TZ v2.0 Section III.5.2 / Task T-064).

DoD:
- Selection of verified public meeting place.
- Warning when deal amount exceeds high_value_meeting_threshold.
- Strict prohibition against releasing private home address.
- Calendar event (.ics) generation with place, time, lot code, amount.
- Outgoing filter checks prevent sensitive internal field leakage.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.meeting_safety import (
    HomeAddressProhibitedError,
    generate_calendar_ics,
    get_high_value_threshold,
    get_meeting_place_by_id,
    is_high_value_deal,
    load_meeting_places,
    propose_meeting_in_thread,
    render_high_value_warning,
    render_meeting_proposal,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing, Lot, Message, Outbox, Thread


def test_load_approved_meeting_places():
    places = load_meeting_places()
    assert len(places) >= 3
    place_ids = [p["id"] for p in places]
    assert "oc_palladium" in place_ids
    assert "hlavni_nadrazi" in place_ids
    assert "police_station_praha1" in place_ids

    palladium = get_meeting_place_by_id("oc_palladium")
    assert palladium is not None
    assert "Náměstí Republiky" in palladium["address"]
    assert palladium["safety_level"] == "PUBLIC_MALL"


def test_high_value_threshold_logic():
    threshold = get_high_value_threshold()
    assert threshold == Decimal("5000.00")

    assert not is_high_value_deal(Decimal("4999.99"))
    assert is_high_value_deal(Decimal("5000.00"))
    assert is_high_value_deal(Decimal("15000.00"))


def test_render_meeting_proposal_cs_and_ru():
    place = get_meeting_place_by_id("oc_palladium")
    assert place is not None

    cs_text = render_meeting_proposal(place, meeting_time="Zítra v 17:00", lang="cs", notes="U hlavního vchodu")
    assert "Dobrý den, navrhuji osobní předání" in cs_text
    assert "OC Palladium" in cs_text
    assert "Zítra v 17:00" in cs_text
    assert "U hlavního vchodu" in cs_text

    ru_text = render_meeting_proposal(place, meeting_time="Завтра в 17:00", lang="ru")
    assert "предлагаю встретиться в публичном месте" in ru_text
    assert "ТЦ Палладиум" in ru_text


def test_prohibit_private_home_address_in_proposal():
    fake_home_place = {
        "id": "my_flat",
        "name_ru": "Моя квартира",
        "address": "Ulice Domácí 12, Praha",
        "safety_level": "PRIVATE_HOME",
    }
    with pytest.raises(HomeAddressProhibitedError):
        render_meeting_proposal(fake_home_place, meeting_time="18:00", allow_home_address=False)


def test_meeting_proposal_filter_blocks_internal_fields():
    place = get_meeting_place_by_id("oc_palladium")
    assert place is not None

    with pytest.raises(InternalFieldLeakageError):
        # Attempting to leak internal price_floor or home_address in proposal context
        render_meeting_proposal(place, meeting_time="18:00", notes="Минимум price_floor: 4000")


def test_render_high_value_warning():
    warning = render_high_value_warning(
        lot_code="GPU-777",
        amount=Decimal("12000.00"),
        currency="CZK",
        recommended_place="Полицейский участок Прага 1",
    )
    assert "[GPU-777]" in warning
    assert "12000.00 CZK" in warning
    assert "Полицейский участок Прага 1" in warning
    assert "Домашний адрес передавать строго запрещено" in warning


def test_generate_calendar_ics():
    dt = datetime(2026, 9, 10, 15, 0, 0, tzinfo=UTC)
    ics = generate_calendar_ics(
        lot_code="ABC",
        place_name="OC Palladium",
        place_address="Náměstí Republiky 1",
        start_dt=dt,
        amount=Decimal("6000.00"),
        currency="CZK",
        counterparty="Petr Buyer",
    )
    assert "BEGIN:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "SUMMARY:NextLife Встреча [ABC] - OC Palladium" in ics
    assert "LOCATION:Náměstí Republiky 1" in ics
    assert "DTSTART:20260910T150000Z" in ics
    assert "DTEND:20260910T153000Z" in ics
    assert "END:VCALENDAR" in ics


def test_propose_meeting_in_thread_with_high_value_flow():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="High Value iPhone Test",
            category_key="general",
            price=Decimal("15000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key="bazos_cz",
            external_id=f"bazos-{lot_code}",
            external_url="https://bazos.cz/item/high",
            price=Decimal("15000.00"),
            currency="CZK",
            price_base=Decimal("15000.00"),
            status="PUBLISHED",
        )
        session.add(listing)
        session.flush()

        thread = Thread(
            id=str(uuid.uuid4()),
            listing_id=listing.id,
            channel="EMAIL",
            counterparty="buyer_safety@example.com",
            lang="cs",
            status="ACTIVE",
        )
        session.add(thread)
        session.commit()

        try:
            res = propose_meeting_in_thread(
                session=session,
                thread_id=thread.id,
                place_id="oc_palladium",
                meeting_time_str="Zítra v 15:00",
                start_dt=datetime(2026, 9, 11, 15, 0, 0, tzinfo=UTC),
                lang="cs",
                notes="U hlavního vchodu",
                actor="TEST_SAFETY",
            )
            session.commit()

            assert res["message_id"] is not None
            assert res["buyer_outbox_id"] is not None
            assert res["owner_warning_id"] is not None, "High value deal must generate owner safety warning"
            assert res["calendar_ics"] is not None
            assert "BEGIN:VCALENDAR" in res["calendar_ics"]

            # Verify buyer message in database
            saved_msg = session.get(Message, res["message_id"])
            assert saved_msg is not None
            assert "OC Palladium" in saved_msg.text_original

            # Verify buyer outbox
            buyer_outbox = session.get(Outbox, res["buyer_outbox_id"])
            assert buyer_outbox is not None
            assert buyer_outbox.recipient_hint == "buyer_safety@example.com"

            # Verify owner warning outbox
            owner_outbox = session.get(Outbox, res["owner_warning_id"])
            assert owner_outbox is not None
            assert owner_outbox.recipient_hint == "OWNER"
            assert "15000.00 CZK" in owner_outbox.body
        finally:
            # Clean up
            session.query(Outbox).filter(Outbox.recipient_hint.in_(["buyer_safety@example.com", "OWNER"])).delete()
            session.query(Message).filter(Message.thread_id == thread.id).delete()
            session.query(Thread).filter(Thread.id == thread.id).delete()
            session.query(Listing).filter(Listing.id == listing.id).delete()
            session.query(Lot).filter(Lot.code == lot_code).delete()
            session.query(Item).filter(Item.code == item_code).delete()
            session.commit()
