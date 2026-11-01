"""Tests for Stage E1 (Домен и коды)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError
from tests.conftest import clean_database

from nextlife.core.approval import (
    ApprovalError,
    compute_payload_hash,
    has_valid_approval,
    record_approval,
)
from nextlife.core.codes import (
    ALL_CODES,
    NoAvailableCodesError,
    allocate_code,
    format_code_display,
    format_code_tag,
    get_allocated_codes,
)
from nextlife.core.status import (
    InvalidStatusTransitionError,
    ItemStatus,
    can_transition,
    validate_transition,
)
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import TruthAssertionError, record_truth
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, TruthLog


def test_code_formatting() -> None:
    assert format_code_display("348") == "#348"
    assert format_code_tag("348") == "[NL-348]"


def test_code_pool_exhaustion() -> None:
    # Verify pool has 1000 codes
    assert len(ALL_CODES) == 1000

    # In a clean database session
    with get_db_session() as session:
        clean_database(session)
        # Pre-allocate all 1000 codes
        release_days = 90
        for code in ALL_CODES:
            item = Item(
                code=code,
                kind="SELL",
                title=f"Item {code}",
                category_key="general",
                attrs={},
                condition_key="used",
                defects={},
                status=ItemStatus.DRAFT.value,
                target_price=Decimal("10.00"),
                target_currency="EUR",
                notes=None,
            )
            session.add(item)
        session.flush()

        # Check allocated codes set
        allocated = get_allocated_codes(session, release_days)
        assert len(allocated) == 1000

        # Attempt to allocate 1001st code must fail
        with pytest.raises(NoAvailableCodesError) as exc_info:
            allocate_code(session, release_days)
        assert "Свободных кодов не осталось" in str(exc_info.value)

        session.rollback()


def test_archived_code_release_after_cooldown() -> None:
    release_days = 90
    test_code = "999"

    with get_db_session() as session:
        # Create an item in ARCHIVED status recently updated (cooldown not expired)
        now = datetime.now(UTC)
        recent_item = Item(
            code=test_code,
            kind="SELL",
            title="Archived Item",
            category_key="general",
            attrs={},
            condition_key="used",
            defects={},
            status=ItemStatus.ARCHIVED.value,
            target_price=Decimal("10.00"),
            target_currency="EUR",
            notes=None,
            updated_at=now - timedelta(days=10),
        )
        session.add(recent_item)
        session.flush()

        # 10 days < 90 days: code should still be allocated
        allocated = get_allocated_codes(session, release_days)
        assert test_code in allocated

        # Update updated_at to 91 days ago: cooldown expired
        recent_item.updated_at = now - timedelta(days=91)
        session.flush()

        # 91 days > 90 days: code should be released back to pool
        allocated_after = get_allocated_codes(session, release_days)
        assert test_code not in allocated_after

        session.rollback()


def test_status_transitions_valid_and_invalid() -> None:
    # Valid transitions
    assert can_transition(ItemStatus.DRAFT, ItemStatus.RESEARCHED) is True
    assert can_transition(ItemStatus.PUBLISHED, ItemStatus.SOLD) is True
    assert can_transition(ItemStatus.SOLD, ItemStatus.ARCHIVED) is True

    # Invalid transitions
    assert can_transition(ItemStatus.DRAFT, ItemStatus.PUBLISHED) is False
    assert can_transition(ItemStatus.ARCHIVED, ItemStatus.DRAFT) is False
    assert can_transition(ItemStatus.SOLD, ItemStatus.DRAFT) is False

    # validate_transition throws on invalid transition
    with pytest.raises(InvalidStatusTransitionError) as exc_info:
        validate_transition(ItemStatus.DRAFT, ItemStatus.PUBLISHED)
    assert "Cannot transition item from 'DRAFT' to 'PUBLISHED'" in str(exc_info.value)

    # validate_transition does not throw on valid transition
    validate_transition(ItemStatus.DRAFT, ItemStatus.RESEARCHED)


def test_approval_with_hash() -> None:
    item_code = "123"
    draft_payload = {
        "title": "Vintage Camera",
        "price": 150.0,
        "marketplaces": ["bazos_cz"],
    }
    canonical_hash = compute_payload_hash(draft_payload)

    with get_db_session() as session:
        # Create item first to satisfy foreign key
        item = Item(
            code=item_code,
            kind="SELL",
            title="Vintage Camera",
            category_key="general",
            attrs={},
            condition_key="used",
            defects={},
            status=ItemStatus.AWAITING_APPROVAL.value,
            target_price=Decimal("150.00"),
            target_currency="EUR",
            notes=None,
        )
        session.add(item)
        session.flush()

        # Mismatched hash fails
        with pytest.raises(ApprovalError):
            record_approval(session, item_code, draft_payload, "invalid_hash", channel="TELEGRAM")

        # Correct hash succeeds
        record_approval(session, item_code, draft_payload, canonical_hash, channel="TELEGRAM")
        session.flush()

        assert has_valid_approval(session, item_code, canonical_hash) is True
        assert has_valid_approval(session, item_code, "different_hash") is False
        session.rollback()


def test_audit_event_update_and_delete_blocked() -> None:
    with get_db_session() as session:
        event = record_audit_event(
            session=session,
            actor="AGENT",
            action="ITEM_CREATED",
            entity="item",
            entity_id="042",
            data={"status": "DRAFT"},
        )
        session.flush()

        # Try to UPDATE -> must fail by trigger
        event.action = "TAMPERED"
        with pytest.raises(DBAPIError) as exc_info:
            session.flush()
        assert "forbidden" in str(exc_info.value).lower()
        session.rollback()


def test_truth_log_requires_evidence_in_db() -> None:
    with get_db_session() as session:
        # Check python-level enforcement
        with pytest.raises(TruthAssertionError):
            record_truth(session, claim="Price is 100 EUR", recipient="OWNER", evidence={})

        # Check DB-level CheckConstraint enforcement
        raw_entry = TruthLog(
            at=datetime.now(UTC),
            claim="Direct DB entry without evidence",
            recipient="OWNER",
            evidence={},
        )
        session.add(raw_entry)
        with pytest.raises(DBAPIError) as exc_info:
            session.flush()
        assert "truth_log_evidence_not_empty" in str(exc_info.value).lower()
        session.rollback()
