from datetime import date, timedelta
from decimal import Decimal

import pytest

from nextlife.core.approval import ApprovalError
from nextlife.pricing.decay import (
    execute_price_update,
    generate_price_decay_proposal,
    is_listing_stale,
)


def test_stale_detection():
    today = date(2026, 9, 8)
    inquiry_10_days_ago = today - timedelta(days=10)
    inquiry_20_days_ago = today - timedelta(days=20)

    assert is_listing_stale(inquiry_10_days_ago, stale_days_threshold=14, current_date=today) is False
    assert is_listing_stale(inquiry_20_days_ago, stale_days_threshold=14, current_date=today) is True


def test_proposal_never_goes_below_price_floor():
    """DoD: A proposal below price_floor is NEVER formed under any circumstances."""
    today = date(2026, 9, 8)
    stale_date = today - timedelta(days=30)

    # Current price 10,000, price floor 9,800.
    # 5% reduction would be 9,500, but floor is 9,800!
    proposal = generate_price_decay_proposal(
        item_code="348",
        current_price=Decimal("10000.00"),
        price_floor=Decimal("9800.00"),
        last_inquiry_at=stale_date,
        decay_steps=[-0.05],
        current_step_index=0,
        current_date=today,
    )
    assert proposal is not None
    assert proposal.proposed_price >= Decimal("9800.00")
    assert proposal.proposed_price == Decimal("9800.00")

    # If current price is already at floor, no proposal can be made
    proposal_at_floor = generate_price_decay_proposal(
        item_code="348",
        current_price=Decimal("9800.00"),
        price_floor=Decimal("9800.00"),
        last_inquiry_at=stale_date,
        current_date=today,
    )
    assert proposal_at_floor is None


def test_price_update_without_approval_fails():
    """DoD: Changing price without approval token raises ApprovalError."""
    with pytest.raises(ApprovalError) as exc_info:
        execute_price_update(
            item_code="348",
            new_price=Decimal("9500.00"),
            price_floor=Decimal("9000.00"),
            approval_token=None,  # Missing approval!
        )
    assert "requires explicit owner approval" in str(exc_info.value)

    # With valid approval token, update succeeds
    updated = execute_price_update(
        item_code="348",
        new_price=Decimal("9500.00"),
        price_floor=Decimal("9000.00"),
        approval_token="owner_token_123",
    )
    assert updated == Decimal("9500.00")
