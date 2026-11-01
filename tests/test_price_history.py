from decimal import Decimal

from sqlalchemy import select

from nextlife.core.approval import compute_payload_hash, record_approval
from nextlife.core.codes import ALL_CODES
from nextlife.core.status import ItemStatus
from nextlife.pricing.history import (
    PriceHistory,
    get_item_price_history,
    record_price_change,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Approval, Item


def _free_item_code(session) -> str:
    # A fixed code (e.g. "993") can be occupied by a random code allocated by
    # another test's API call and not cleaned up — pick an unused one instead.
    used = set(session.scalars(select(Item.code)).all())
    for code in ALL_CODES:
        if code not in used:
            return code
    raise AssertionError("no free item code")


def test_price_change_records_history_and_invalidates_approval():
    """DoD: Price change records history with initiator and reason, and invalidates draft approval."""
    with get_db_session() as session:
        code = _free_item_code(session)
        try:
            # 1. Create item in APPROVED status with valid approval record
            item = Item(
                code=code,
                kind="SELL",
                title="Test RTX Card",
                category_key="hardware",
                attrs={},
                condition_key="used",
                defects={},
                status=ItemStatus.APPROVED.value,
                target_price=Decimal("15000.00"),
                target_currency="CZK",
            )
            session.add(item)
            session.commit()

            # Record initial approval
            payload = {"item_code": code, "price": 15000}
            payload_hash = compute_payload_hash(payload)
            record_approval(session, code, payload, payload_hash, channel="WEB")
            session.commit()

            # Verify approval exists
            approvals_before = session.query(Approval).filter_by(item_code=code).all()
            assert len(approvals_before) == 1
            assert item.status == ItemStatus.APPROVED.value

            # 2. Modify price: e.g. lower to 14,000 CZK
            record_price_change(
                session,
                item_code=code,
                new_price=Decimal("14000.00"),
                initiator="OWNER",
                reason="Stale listing 5% decay",
            )

            # 3. DoD Verification:
            # - History is recorded with initiator and reason
            history = get_item_price_history(session, code)
            assert len(history) == 1
            assert history[0]["old_price"] == Decimal("15000.00")
            assert history[0]["new_price"] == Decimal("14000.00")
            assert history[0]["initiator"] == "OWNER"
            assert history[0]["reason"] == "Stale listing 5% decay"

            # - Approval is strictly invalidated (deleted and status reverted to DRAFT)
            approvals_after = session.query(Approval).filter_by(item_code=code).all()
            assert len(approvals_after) == 0

            # Reload item to verify status reverted
            session.refresh(item)
            assert item.status == ItemStatus.DRAFT.value
            assert item.target_price == Decimal("14000.00")
        finally:
            session.query(PriceHistory).filter_by(item_code=code).delete()
            session.query(Approval).filter_by(item_code=code).delete()
            session.query(Item).filter_by(code=code).delete()
            session.commit()
