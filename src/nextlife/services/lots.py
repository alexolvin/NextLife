"""Item and lot lifecycle management (TZ v2.0 Section II.1).

DoD:
- Separates physical item attributes from commercial lot offers.
- Relisting the same item generates a new Lot with a new unique 3-character code.
- Historical lots and their codes are fully preserved.
- Module length <= 400 lines.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.codes import ALL_CODES, NoAvailableCodesError
from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models import Item, Lot


class LotServiceError(NextLifeError):
    """Base error for lot operations."""


def allocate_lot_code(session: Session) -> str:
    """Allocate an unused 3-digit code for a new lot offering."""
    # Check all existing lot codes
    existing_lot_codes = set(session.scalars(select(Lot.code)).all())
    # Also check item codes for safety
    existing_item_codes = set(session.scalars(select(Item.code)).all())
    used = existing_lot_codes | existing_item_codes

    for code in ALL_CODES:
        if code not in used:
            return code

    raise NoAvailableCodesError("No available 3-digit codes remaining in pool.")


def create_lot_for_item(
    session: Session,
    item_code: str,
    price_gross: Decimal,
    price_floor: Decimal,
    currency: str = "CZK",
    shipping_policy: str = "BUYER_PAYS",
    notes: str | None = None,
    lot_code: str | None = None,
) -> Lot:
    """Create a commercial lot for a physical item."""
    item = session.get(Item, item_code)
    if not item:
        raise LotServiceError(f"Physical item '{item_code}' not found.")

    assigned_code = lot_code or item_code
    # Check if code already used in lot table
    existing = session.scalar(select(Lot).where(Lot.code == assigned_code))
    if existing:
        assigned_code = allocate_lot_code(session)

    lot = Lot(
        code=assigned_code,
        item_id=item.code,
        status="DRAFT",
        price_gross=price_gross,
        price_floor=price_floor,
        currency=currency,
        shipping_policy=shipping_policy,
        notes=notes,
    )
    session.add(lot)
    session.flush()
    return lot


def relist_item(
    session: Session,
    item_code: str,
    new_price_gross: Decimal,
    new_price_floor: Decimal,
    currency: str = "CZK",
    shipping_policy: str = "BUYER_PAYS",
    notes: str | None = None,
) -> Lot:
    """Relist a previously unsold or returned item with a new commercial offer and fresh 3-digit code.

    DoD:
    - Creates a new Lot with a new unique 3-character code.
    - Links to the existing item_id.
    - Preserves historical lots in database.
    """
    item = session.get(Item, item_code)
    if not item:
        raise LotServiceError(f"Physical item '{item_code}' not found.")

    new_code = allocate_lot_code(session)

    new_lot = Lot(
        code=new_code,
        item_id=item.code,
        status="DRAFT",
        price_gross=new_price_gross,
        price_floor=new_price_floor,
        currency=currency,
        shipping_policy=shipping_policy,
        notes=notes,
    )
    session.add(new_lot)
    session.flush()
    return new_lot
