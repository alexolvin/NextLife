"""Lot reservation service and DB-level double-sale prevention (TZ v2.0 Section II.4).

DoD:
- Transition lot to RESERVED with reserved_for_party_id and reserved_until.
- Auto-reply generation informs counterparty about reservation until specified time.
- Attempt to create a second deal on a reserved lot strictly fails with a DB error (UniqueViolation / DB constraint).
- Expiration of reservation returns lot to PUBLISHED, cancels/expires deal, and logs audit.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.comms.outgoing_filter import render_outgoing_template
from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ItemStatus, can_transition, validate_transition
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Deal, Item, Listing, Lot, Party

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "comms"


class ReservationError(NextLifeError):
    """Base error for reservation service operations."""


def reserve_lot(
    session: Session,
    lot_code: str,
    party_id: str,
    *,
    agreed_price: Decimal | None = None,
    reservation_hours: int | None = None,
    actor: str = "AGENT",
) -> dict[str, Any]:
    """Reserve a lot for a specific party for specified duration.

    Creates a deal in RESERVED status and sets lot status to RESERVED.
    """
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ReservationError(f"Lot with code '{lot_code}' not found")

    item = session.get(Item, lot.item_id)
    if not item:
        raise ReservationError(f"Item '{lot.item_id}' not found")

    # Validate transition for item
    if can_transition(item.status, ItemStatus.RESERVED):
        validate_transition(ItemStatus(item.status), ItemStatus.RESERVED)
        item.status = ItemStatus.RESERVED.value
    else:
        # If item status is already RESERVED or not allowed
        if item.status != ItemStatus.RESERVED.value:
            validate_transition(ItemStatus(item.status), ItemStatus.RESERVED)

    # Determine reservation duration
    cfg = load_config()
    hours: int = int(reservation_hours or getattr(cfg.app, "reservation_hours", 24) or 24)
    now_utc = datetime.now(UTC)
    reserved_until = now_utc + timedelta(hours=hours)

    lot.status = ItemStatus.RESERVED.value
    lot.reserved_for_party_id = party_id
    lot.reserved_until = reserved_until

    # Find primary listing if any
    listing = session.scalar(select(Listing).where(Listing.item_code == item.code))
    listing_id = listing.id if listing else None

    # Ensure Party exists for party_id
    if party_id:
        existing_party = session.get(Party, party_id)
        if not existing_party:
            new_party = Party(id=party_id, name=f"Party {party_id}")
            session.add(new_party)
            session.flush()

    # Create first Deal record associated with this reservation
    price_val = agreed_price or lot.price_gross or item.target_price or Decimal("0.00")
    deal = Deal(
        id=str(uuid.uuid4()),
        lot_id=lot.id,
        party_id=party_id,
        listing_id=listing_id,
        agreed_price=price_val,
        currency=lot.currency,
        price_base=price_val,
        delivery_method="standard",
        payment_method="bank_transfer",
        status="RESERVED",
    )
    session.add(deal)
    session.flush()

    # Record truth assertion
    record_truth(
        session=session,
        claim=f"Лот #{lot.code} успешно зарезервирован за покупателем {party_id} до {reserved_until.strftime('%Y-%m-%d %H:%M UTC')}",
        recipient="OWNER",
        evidence={
            "lot_code": lot.code,
            "party_id": party_id,
            "reserved_until": reserved_until.isoformat(),
            "deal_id": deal.id,
        },
    )

    # Record audit event
    record_audit_event(
        session=session,
        actor=actor,
        action="LOT_RESERVED",
        entity="lot",
        entity_id=lot.code,
        data={
            "party_id": party_id,
            "reserved_until": reserved_until.isoformat(),
            "deal_id": deal.id,
            "agreed_price": str(price_val),
            "currency": lot.currency,
        },
    )

    return {
        "lot_code": lot.code,
        "lot_status": lot.status,
        "party_id": party_id,
        "reserved_until": reserved_until,
        "deal_id": deal.id,
    }


def create_deal_for_lot(
    session: Session,
    lot_id: str,
    party_id: str,
    agreed_price: Decimal,
    *,
    currency: str = "CZK",
    price_base: Decimal | None = None,
    delivery_method: str = "standard",
    payment_method: str = "bank_transfer",
    status: str = "ACTIVE",
    listing_id: str | None = None,
) -> Deal:
    """Attempt to create a deal for a lot.

    If the lot is already reserved or has an active deal, DB constraints/triggers
    strictly reject the second deal with a database-level integrity error.
    """
    if party_id:
        existing_party = session.get(Party, party_id)
        if not existing_party:
            new_party = Party(id=party_id, name=f"Party {party_id}")
            session.add(new_party)
            session.flush()

    deal = Deal(
        id=str(uuid.uuid4()),
        lot_id=lot_id,
        party_id=party_id,
        listing_id=listing_id,
        agreed_price=agreed_price,
        currency=currency,
        price_base=price_base or agreed_price,
        delivery_method=delivery_method,
        payment_method=payment_method,
        status=status,
    )
    session.add(deal)
    session.flush()
    return deal


def generate_reservation_auto_reply(
    lot: Lot,
    *,
    lang: str = "cs",
) -> str:
    """Generate safe counterparty auto-reply informing about active lot reservation.

    Passes strict outgoing filter against internal field leaks.
    """
    formatted_time = (
        lot.reserved_until.strftime("%d.%m.%Y %H:%M UTC")
        if lot.reserved_until
        else "neuvedeno"
    )

    template_file = TEMPLATE_DIR / f"reservation_reply_{lang}.jinja2"
    if not template_file.exists():
        template_file = TEMPLATE_DIR / "reservation_reply_cs.jinja2"

    template_str = template_file.read_text(encoding="utf-8")
    context = {"reserved_until": formatted_time}

    return render_outgoing_template(template_str, context)


def release_expired_reservation(
    session: Session,
    lot_code: str,
    *,
    force: bool = False,
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Release reservation when time expires without payment.

    Reverts lot and item status to PUBLISHED and marks reservation deal as EXPIRED.
    """
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ReservationError(f"Lot with code '{lot_code}' not found")

    if lot.status != ItemStatus.RESERVED.value:
        return {"lot_code": lot.code, "released": False, "reason": "Not reserved"}

    now_utc = datetime.now(UTC)
    if not force and lot.reserved_until and now_utc < lot.reserved_until:
        return {
            "lot_code": lot.code,
            "released": False,
            "reason": f"Reservation not yet expired (until {lot.reserved_until.isoformat()})",
        }

    # Transition item back to PUBLISHED
    item = session.get(Item, lot.item_id)
    if item:
        validate_transition(ItemStatus(item.status), ItemStatus.PUBLISHED)
        item.status = ItemStatus.PUBLISHED.value

    old_party = lot.reserved_for_party_id
    lot.status = ItemStatus.PUBLISHED.value
    lot.reserved_for_party_id = None
    lot.reserved_until = None

    # Mark active deals for this lot as EXPIRED so new deals can be made
    deals = session.scalars(
        select(Deal).where(Deal.lot_id == lot.id, Deal.status == "RESERVED")
    ).all()
    expired_deal_ids = []
    for d in deals:
        d.status = "EXPIRED"
        d.closed_at = now_utc
        expired_deal_ids.append(d.id)

    session.flush()

    # Record truth log
    record_truth(
        session=session,
        claim=f"Резерв лота #{lot.code} истёк без оплаты. Лот возвращён в статус PUBLISHED.",
        recipient="OWNER",
        evidence={
            "lot_code": lot.code,
            "released_from_party": old_party,
            "expired_deals": expired_deal_ids,
        },
    )

    # Record audit event
    record_audit_event(
        session=session,
        actor=actor,
        action="RESERVATION_EXPIRED",
        entity="lot",
        entity_id=lot.code,
        data={
            "previous_party": old_party,
            "expired_deal_ids": expired_deal_ids,
            "new_lot_status": lot.status,
        },
    )

    return {
        "lot_code": lot.code,
        "released": True,
        "new_status": lot.status,
        "expired_deals": expired_deal_ids,
    }
