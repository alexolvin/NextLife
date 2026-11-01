"""Tests for escalation workflow with translation and owner command control (T-055, Section V.4).

DoD:
- Package incoming message with original text, Russian translation, and prepared draft response.
- Formats owner notification with mandatory [lot_code] prefix.
- Prepared response strictly NEVER dispatches to the counterparty without an explicit owner command.
- Owner commands: SEND (approve prepared), EDIT (custom response), DECLINE (dismiss).
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.services.escalation import (
    EscalationError,
    create_owner_escalation,
    execute_owner_command,
    get_escalation_details,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Listing, Message, Outbox, Thread


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def _create_escalation_fixture(session, original_text="Dobrý den, je možná sleva na 3000 Kč?", lang="cs"):
    created = create_item_and_lot(
        session=session,
        title="Escalation Test Item",
        category_key="general",
        price=Decimal("4000.00"),
    )
    lot_code = created["lot_code"]
    item_code = created["item_code"]
    unique_buyer = f"buyer-{uuid.uuid4().hex[:8]}@example.com"

    listing = Listing(
        id=str(uuid.uuid4()),
        item_code=item_code,
        marketplace_key="bazos_cz",
        external_id=f"bazos-{lot_code}",
        external_url="https://bazos.cz/item/123",
        price=Decimal("4000.00"),
        currency="CZK",
        price_base=Decimal("4000.00"),
        status="PUBLISHED",
        published_at=datetime.now(UTC),
    )
    session.add(listing)
    session.flush()

    thread = Thread(
        id=str(uuid.uuid4()),
        listing_id=listing.id,
        channel="bazos_cz",
        counterparty=unique_buyer,
        lang=lang,
        status="ACTIVE",
    )
    session.add(thread)
    session.flush()

    msg = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        direction="IN",
        transport="BAZOS_CZ",
        text_original=original_text,
        lang_original=lang,
        sent_at=datetime.now(UTC),
    )
    session.add(msg)
    session.flush()

    return lot_code, item_code, thread, msg


def test_create_owner_escalation_packages_translation_and_draft(db):
    lot_code, item_code, thread, msg = _create_escalation_fixture(db)

    res = create_owner_escalation(
        session=db,
        message_id=msg.id,
        reason="bargain_request",
        prepared_response="Dobrý den, cena je pevná 4000 Kč.",
    )

    assert res["lot_code"] == lot_code
    assert res["status"] == "AWAITING_OWNER_COMMAND"
    assert res["prepared_response"] == "Dobrý den, cena je pevná 4000 Kč."
    assert f"[{lot_code}]" in res["owner_notification"]

    # Verify message state
    db.refresh(msg)
    assert msg.escalated is True
    assert msg.answered_by is None  # Must NOT be marked answered
    assert msg.text_ru is not None
    assert msg.source_evidence["escalation_status"] == "AWAITING_OWNER_COMMAND"
    assert msg.source_evidence["prepared_response"] == "Dobrý den, cena je pevná 4000 Kč."

    # Verify outbox has notification to OWNER, but STRICTLY NO message to counterparty
    owner_entry = db.scalar(
        select(Outbox).where(Outbox.idempotency_key == f"owner-notify-{msg.id}")
    )
    buyer_entries = db.scalars(
        select(Outbox).where(Outbox.recipient_hint == thread.counterparty)
    ).all()

    assert owner_entry is not None
    assert f"[{lot_code}]" in owner_entry.body
    assert len(buyer_entries) == 0, "DoD violation: prepared response dispatched before owner command!"


def test_owner_command_send_dispatches_prepared_response(db):
    lot_code, item_code, thread, msg = _create_escalation_fixture(db)
    create_owner_escalation(
        session=db,
        message_id=msg.id,
        reason="bargain_request",
        prepared_response="Dobrý den, cena je pevná 4000 Kč.",
    )

    # Owner executes SEND
    cmd_res = execute_owner_command(session=db, message_id=msg.id, command="SEND")
    assert cmd_res["status"] == "SENT"
    assert cmd_res["sent_text"] == "Dobrý den, cena je pevná 4000 Kč."

    db.refresh(msg)
    assert msg.answered_by == "OWNER"
    assert msg.source_evidence["escalation_status"] == "SENT"

    # Verify outbox now has message for buyer
    buyer_entries = db.scalars(
        select(Outbox).where(Outbox.recipient_hint == thread.counterparty)
    ).all()
    assert len(buyer_entries) == 1
    assert buyer_entries[0].body == "Dobrý den, cena je pevná 4000 Kč."

    # Verify new OUT message added to thread
    out_msgs = db.scalars(
        select(Message).where(Message.thread_id == thread.id, Message.direction == "OUT")
    ).all()
    assert len(out_msgs) == 1
    assert out_msgs[0].text_original == "Dobrý den, cena je pevná 4000 Kč."
    assert out_msgs[0].answered_by == "OWNER"


def test_owner_command_edit_dispatches_custom_text(db):
    lot_code, item_code, thread, msg = _create_escalation_fixture(db)
    create_owner_escalation(
        session=db,
        message_id=msg.id,
        reason="bargain_request",
        prepared_response="Dobrý den, cena je pevná 4000 Kč.",
    )

    custom_reply = "Dobrý den, mohu slevit na 3700 Kč při rychlém odběru dnes."
    cmd_res = execute_owner_command(
        session=db,
        message_id=msg.id,
        command="EDIT",
        custom_text=custom_reply,
    )
    assert cmd_res["status"] == "EDITED_AND_SENT"
    assert cmd_res["sent_text"] == custom_reply

    db.refresh(msg)
    assert msg.answered_by == "OWNER"
    assert msg.source_evidence["escalation_status"] == "EDITED_AND_SENT"

    # Verify outbox has custom text for buyer
    buyer_entries = db.scalars(
        select(Outbox).where(Outbox.recipient_hint == thread.counterparty)
    ).all()
    assert len(buyer_entries) == 1
    assert buyer_entries[0].body == custom_reply


def test_owner_command_decline_dismisses_without_dispatch(db):
    lot_code, item_code, thread, msg = _create_escalation_fixture(db)
    create_owner_escalation(
        session=db,
        message_id=msg.id,
        reason="spam_or_irrelevant",
        prepared_response="Default answer",
    )

    cmd_res = execute_owner_command(session=db, message_id=msg.id, command="DECLINE")
    assert cmd_res["status"] == "DISMISSED"
    assert cmd_res["sent_text"] is None

    db.refresh(msg)
    assert msg.answered_by == "OWNER"
    assert msg.source_evidence["escalation_status"] == "DECLINED"

    # Verify NO buyer message dispatched
    buyer_entries = db.scalars(
        select(Outbox).where(Outbox.recipient_hint == thread.counterparty)
    ).all()
    assert len(buyer_entries) == 0


def test_escalation_error_handling(db):
    lot_code, item_code, thread, msg = _create_escalation_fixture(db)
    create_owner_escalation(session=db, message_id=msg.id)

    # Unknown command
    with pytest.raises(EscalationError, match="Unknown owner command"):
        execute_owner_command(session=db, message_id=msg.id, command="INVALID_CMD")

    # EDIT with empty text
    with pytest.raises(EscalationError, match="EDIT command requires non-empty custom_text"):
        execute_owner_command(session=db, message_id=msg.id, command="EDIT", custom_text="   ")

    # Non-existent message
    with pytest.raises(EscalationError, match="not found"):
        create_owner_escalation(session=db, message_id="non-existent-id")


def test_get_escalation_details(db):
    lot_code, item_code, thread, msg = _create_escalation_fixture(db)
    create_owner_escalation(
        session=db,
        message_id=msg.id,
        reason="bargain_request",
        prepared_response="Dobrý den, cena je pevná.",
    )

    details = get_escalation_details(session=db, message_id=msg.id)
    assert details["message_id"] == msg.id
    assert details["thread_id"] == thread.id
    assert details["lot_code"] == lot_code
    assert details["counterparty"] == thread.counterparty
    assert details["prepared_response"] == "Dobrý den, cena je pevná."
    assert details["status"] == "AWAITING_OWNER_COMMAND"
