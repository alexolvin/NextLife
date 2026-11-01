"""Tests for Stage E6 (Коммуникации)."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from tests.conftest import clean_database

from nextlife.comms.classifier import (
    extract_item_code_from_subject,
    process_incoming_message,
    resolve_message_item_code,
)
from nextlife.comms.translation import detect_language, translate_text
from nextlife.core.config import load_config
from nextlife.core.status import ItemStatus
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing, Thread, TruthLog


def create_test_item_with_notes(session: Session, code: str, notes: str | None = None) -> Item:
    item = Item(
        code=code,
        kind="SELL",
        title=f"Vintage Lens {code}",
        category_key="electronics",
        attrs={"pickup_allowed": True},
        condition_key="used",
        defects={},
        status=ItemStatus.PUBLISHED.value,
        target_price=Decimal("120.00"),
        target_currency="EUR",
        notes=notes,
    )
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def clean_db_session() -> Session:
    with get_db_session() as session:
        clean_database(session)
        yield session
        clean_database(session)


def test_subject_marker_extraction() -> None:
    assert extract_item_code_from_subject("Dotaz na inzerát [NL-348]") == "348"
    assert extract_item_code_from_subject("Re: [NL-001] Kamera") == "001"
    assert extract_item_code_from_subject("Obecný dotaz bez kódu") is None


def test_language_detection_and_translation() -> None:
    assert detect_language("Dobrý den, je to ještě volné?") == "cs"
    assert detect_language("Здравствуйте, актуально ли объявление?") == "ru"
    assert detect_language("Hello, is this item still available?") == "en"

    same = translate_text("Ahoj", "cs", "cs")
    assert same == "Ahoj"


def test_faq_auto_reply_records_truth_log(clean_db_session: Session) -> None:
    session = clean_db_session
    code = "348"
    create_test_item_with_notes(session, code)
    cfg = load_config()

    res = process_incoming_message(
        session=session,
        sender="buyer@example.cz",
        subject="Dotaz na inzerát [NL-348]",
        body="Dobrý den, je to ještě volné?",
        channel="EMAIL",
        config=cfg,
    )

    assert res["type"] == "AUTO_REPLY"
    assert res["item_code"] == code
    assert res["source_field"] == "status"
    assert "k dispozici" in res["response_text"]

    # Verify truth_log record exists with evidence
    tlog = (
        session.query(TruthLog)
        .filter(TruthLog.recipient == "COUNTERPARTY")
        .order_by(TruthLog.at.desc())
        .first()
    )
    assert tlog is not None
    assert tlog.evidence.get("source_field") == "status"
    assert tlog.evidence.get("source_field_value") == "PUBLISHED"


def test_pricing_discount_query_escalates_to_owner(clean_db_session: Session) -> None:
    session = clean_db_session
    code = "349"
    create_test_item_with_notes(session, code)
    cfg = load_config()

    res = process_incoming_message(
        session=session,
        sender="bargain_hunter@example.cz",
        subject="Nabídka [NL-349]",
        body="Dobrý den, dáte slevu 20% při rychlém jednání?",
        channel="EMAIL",
        config=cfg,
    )

    assert res["type"] == "ESCALATION"
    assert res["item_code"] == code
    assert res["requires_owner_approval"] is True
    assert res["reason"] == "manual_decision_or_unmatched_faq"


def test_missing_card_data_escalates_not_guesses(clean_db_session: Session) -> None:
    session = clean_db_session
    code = "350"
    # Create item with notes=None (pickup location unknown)
    create_test_item_with_notes(session, code, notes=None)
    cfg = load_config()

    res = process_incoming_message(
        session=session,
        sender="buyer2@example.cz",
        subject="Dotaz [NL-350]",
        body="Kde je možné předání?",
        channel="EMAIL",
        config=cfg,
    )

    # Must NOT guess location; must escalate because notes is empty
    assert res["type"] == "ESCALATION"
    assert res["item_code"] == code
    assert res["requires_owner_approval"] is True


def test_ambiguous_message_without_marker_escalates(clean_db_session: Session) -> None:
    session = clean_db_session
    code1 = "351"
    code2 = "352"
    item1 = create_test_item_with_notes(session, code1)
    item2 = create_test_item_with_notes(session, code2)

    # Create active listings for both items
    list1 = Listing(
        id="list-1",
        item_code=item1.code,
        marketplace_key="bazos_cz",
        price=Decimal("10.00"),
        currency="EUR",
        price_base=Decimal("10.00"),
        status="ACTIVE",
        publish_evidence={"mock": True},
    )
    list2 = Listing(
        id="list-2",
        item_code=item2.code,
        marketplace_key="bazos_cz",
        price=Decimal("20.00"),
        currency="EUR",
        price_base=Decimal("20.00"),
        status="ACTIVE",
        publish_evidence={"mock": True},
    )
    session.add_all([list1, list2])
    session.commit()

    # Same sender has active threads on both items
    thread1 = Thread(
        id="th-1",
        listing_id=list1.id,
        channel="EMAIL",
        counterparty="multi_buyer@example.cz",
        lang="cs",
        status="OPEN",
    )
    thread2 = Thread(
        id="th-2",
        listing_id=list2.id,
        channel="EMAIL",
        counterparty="multi_buyer@example.cz",
        lang="cs",
        status="OPEN",
    )
    session.add_all([thread1, thread2])
    session.commit()

    # Incoming message without marker
    code, status = resolve_message_item_code(session, "multi_buyer@example.cz", "Dobrý den")
    assert status == "AMBIGUOUS"
    assert code is None

    res = process_incoming_message(
        session=session,
        sender="multi_buyer@example.cz",
        subject="Dotaz k inzerátu",
        body="Mám zájem.",
    )
    assert res["type"] == "ESCALATION"
    assert res["reason"] == "ambiguous_subject_or_unknown_item"


def test_outbox_lifecycle_and_idempotency(clean_db_session: Session) -> None:
    from nextlife.comms.outbox import OutboxError, ack_outbox, enqueue_outbox, pull_outbox
    from nextlife.storage.models import Outbox

    session = clean_db_session
    session.query(Outbox).delete()
    session.commit()

    # Enqueue message
    msg = enqueue_outbox(
        session=session,
        transport="TELEGRAM",
        body="[348]: Привет Владелец",
        idempotency_key="out-123",
        recipient_hint="OWNER",
    )
    session.commit()
    assert msg.state == "PENDING"

    # Enqueue same key -> idempotent
    dup = enqueue_outbox(
        session=session,
        transport="TELEGRAM",
        body="Duplicate",
        idempotency_key="out-123",
    )
    assert dup.id == msg.id

    # Pull outbox
    pulled = pull_outbox(session, retry_timeout_seconds=0)
    session.commit()
    assert len(pulled) == 1
    assert pulled[0]["id"] == msg.id
    assert pulled[0]["transport"] == "TELEGRAM"

    # Ack without evidence -> must fail
    with pytest.raises(OutboxError):
        ack_outbox(session, msg.id, delivery_evidence={})

    # Ack with evidence -> SENT
    acked = ack_outbox(session, msg.id, delivery_evidence={"telegram_msg_id": 9999})
    session.commit()
    assert acked.state == "SENT"
    assert acked.delivery_evidence == {"telegram_msg_id": 9999}


def test_inbox_push_deduplication(clean_db_session: Session) -> None:
    from nextlife.comms.inbox import handle_inbox_push
    from nextlife.storage.models import Message

    session = clean_db_session
    session.query(Message).delete()
    session.commit()

    payload = {
        "message_id": "msg-unique-test-456",
        "from": "buyer@example.com",
        "subject": "Dotaz",
        "body": "Dobry den",
    }

    res1 = handle_inbox_push(session, payload)
    assert res1["status"] in ("auto_replied", "escalated")

    # Second push with same message_id -> must be duplicate
    res2 = handle_inbox_push(session, payload)
    assert res2["status"] == "duplicate"
    assert res2["message_id"] == "msg-unique-test-456"


def test_channel_contract_zero_credentials() -> None:
    import subprocess

    cmd = 'grep -rniE "imap|smtp|group_id|chat_id|bot_token" src/ config/'
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert proc.returncode != 0, f"Found forbidden channel credentials: {proc.stdout}"
