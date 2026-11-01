"""Price decay and stale listing recommendation engine (TZ v2.0 Section I.3, III.1)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from nextlife.core.approval import ApprovalError


class PriceDecayProposal(BaseModel):
    item_code: str
    current_price: Decimal
    proposed_price: Decimal
    decay_percent: float
    stale_days: int
    price_floor: Decimal = Field(..., description="Internal floor price")
    requires_approval: bool = True
    explanation: str


def is_listing_stale(
    last_inquiry_at: date | datetime,
    stale_days_threshold: int = 14,
    current_date: date | None = None,
) -> bool:
    """Detect if a listing has been stale without inquiries for longer than threshold."""
    if isinstance(last_inquiry_at, datetime):
        last_date = last_inquiry_at.date()
    else:
        last_date = last_inquiry_at

    ref_date = current_date or datetime.now(UTC).date()
    return (ref_date - last_date).days >= stale_days_threshold


def generate_price_decay_proposal(
    item_code: str,
    current_price: Decimal,
    price_floor: Decimal,
    last_inquiry_at: date | datetime,
    decay_steps: list[float] | None = None,
    current_step_index: int = 0,
    stale_days_threshold: int = 14,
    current_date: date | None = None,
) -> PriceDecayProposal | None:
    """Generate recommendation to lower price on stale listing.

    DoD:
    - Strictly checks price_floor: a proposal below price_floor is NEVER formed.
    - System proposes, never automatically modifies without owner approval.
    """
    if not is_listing_stale(last_inquiry_at, stale_days_threshold, current_date):
        return None

    steps = decay_steps or [-0.05, -0.10, -0.15]
    if current_step_index >= len(steps):
        step_val = steps[-1]
    else:
        step_val = steps[current_step_index]

    # Calculate reduction: e.g. -0.05 -> 0.95
    multiplier = Decimal(str(1.0 + step_val))
    raw_proposed = (current_price * multiplier).quantize(Decimal("1.00"))

    # DoD: A proposal below price_floor is NEVER formed.
    if raw_proposed < price_floor:
        # If current price is already at or below floor, no proposal can be made
        if current_price <= price_floor:
            return None
        # Cap at price_floor
        proposed_price = price_floor
    else:
        proposed_price = raw_proposed

    # Extra safety check: strictly assert proposed_price >= price_floor
    if proposed_price < price_floor:
        return None

    actual_pct = float((proposed_price - current_price) / current_price * 100)

    ref_date = current_date or datetime.now(UTC).date()
    last_date = last_inquiry_at.date() if isinstance(last_inquiry_at, datetime) else last_inquiry_at
    days_stale = (ref_date - last_date).days

    return PriceDecayProposal(
        item_code=item_code,
        current_price=current_price,
        proposed_price=proposed_price,
        decay_percent=actual_pct,
        stale_days=days_stale,
        price_floor=price_floor,
        requires_approval=True,
        explanation=(
            f"Лот #{item_code} без обращений уже {days_stale} дн. "
            f"Предлагается снизить цену с {current_price:.0f} до {proposed_price:.0f} Kč "
            f"({actual_pct:.1f}%) для оживления спроса."
        ),
    )


def execute_price_update(
    item_code: str,
    new_price: Decimal,
    price_floor: Decimal,
    approval_token: str | None,
) -> Decimal:
    """Execute price update with mandatory approval token check.

    DoD: Price change without approval is strictly impossible.
    """
    if not approval_token or not approval_token.strip():
        raise ApprovalError(f"Price change for item #{item_code} requires explicit owner approval token.")

    if new_price < price_floor:
        raise ValueError(f"Cannot set price {new_price} below reserve price_floor {price_floor}.")

    return new_price
