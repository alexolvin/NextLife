"""Fulfillment service for releasing pickup address and tracking number (TZ v2.0 Section III.3.3).

DoD:
- Pickup address and carrier tracking number released ONLY after transition to PAID.
- Exceptions:
  1. Cash on pickup (CASH / CASH_ON_PICKUP) allows address release upon RESERVED / scheduling.
  2. Cash on delivery (COD / DOBIRKA) allows tracking number release upon DISPATCHED.
- Attempt to dispatch home address before PAID strictly blocked by outgoing filter.
- Module length <= 400 lines.
"""

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.comms.outgoing_filter import (
    InternalFieldLeakageError,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Item
from nextlife.storage.models.deals import Deal, Handover
from nextlife.storage.models.lots import Lot


def _storage_location_for_deal(session: Session, deal: Deal) -> str | None:
    """ТЗ «Склад» §3.6: место на складе предмета сделки."""
    if not deal.lot_id:
        return None
    lot = session.get(Lot, deal.lot_id)
    if not lot:
        return None
    item = session.get(Item, lot.item_id)
    return item.storage_location if item else None

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "comms"
PICKUP_TEMPLATE = TEMPLATE_DIR / "pickup_address.jinja2"
TRACKING_TEMPLATE = TEMPLATE_DIR / "tracking_info.jinja2"


class FulfillmentSecurityError(NextLifeError):
    """Raised when fulfillment details are accessed or released prematurely."""


def release_pickup_address(
    session: Session,
    deal_id: str,
    address: str,
    *,
    instructions: str | None = None,
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Release pickup address to counterparty following payment confirmation rules.

    DoD:
    - Blocked if deal is not PAID, unless payment method is CASH on pickup.
    - Prevent leakage through outgoing filter.
    """
    deal = session.get(Deal, deal_id)
    if not deal:
        raise FulfillmentSecurityError(f"Deal '{deal_id}' not found")

    pay_method = (deal.payment_method or "").upper()
    is_cash_pickup = pay_method in ("CASH", "CASH_ON_PICKUP")

    # If not cash on pickup, deal MUST be PAID
    if not is_cash_pickup and deal.status != "PAID":
        raise InternalFieldLeakageError(
            f"Security policy violation: attempt to release home address for deal #{deal.id} before payment is confirmed (status: '{deal.status}')."
        )

    # Get or create handover record
    handover = deal.handover
    if not handover:
        handover = Handover(
            deal_id=deal.id,
            method="PERSONAL",
            place=address,
            status="SCHEDULED",
        )
        session.add(handover)
    else:
        handover.place = address
        if handover.status == "PENDING":
            handover.status = "SCHEDULED"

    lot_code = "???"
    if deal.lot_id:
        lot = session.get(Lot, deal.lot_id)
        if lot:
            lot_code = lot.code

    # Render through Jinja2 template
    tmpl_content = PICKUP_TEMPLATE.read_text(encoding="utf-8")
    context = {
        "lot_code": lot_code,
        "address": address,
        "instructions": instructions,
    }
    rendered_message = render_outgoing_template(tmpl_content, context)

    record_truth(
        session=session,
        claim=f"Адрес самовывоза для сделки #{deal.id} успешно выдан (метод оплаты: {pay_method}).",
        recipient="OWNER",
        evidence={"deal_id": deal.id, "lot_code": lot_code, "status": deal.status},
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="PICKUP_ADDRESS_RELEASED",
        entity="deal",
        entity_id=deal.id,
        data={"lot_code": lot_code, "is_cash_pickup": is_cash_pickup, "deal_status": deal.status},
    )

    session.flush()

    return {
        "deal_id": deal.id,
        "lot_code": lot_code,
        "address": address,
        "message": rendered_message,
        "storage_location": _storage_location_for_deal(session, deal),
    }


def release_tracking_number(
    session: Session,
    deal_id: str,
    tracking_number: str,
    *,
    carrier: str = "Zásilkovna",
    tracking_url: str | None = None,
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Release parcel tracking number to counterparty.

    DoD:
    - Blocked if deal is not PAID, unless payment method is COD (Dobírka).
    """
    deal = session.get(Deal, deal_id)
    if not deal:
        raise FulfillmentSecurityError(f"Deal '{deal_id}' not found")

    pay_method = (deal.payment_method or "").upper()
    is_cod = pay_method in ("COD", "DOBIRKA")

    # If not COD, deal MUST be PAID (or already closed/dispatched)
    if not is_cod and deal.status not in ("PAID", "DISPATCHED", "COMPLETED"):
        raise FulfillmentSecurityError(
            f"Cannot release tracking number for deal #{deal.id} before payment is confirmed (current status: '{deal.status}')."
        )

    handover = deal.handover
    if not handover:
        handover = Handover(
            deal_id=deal.id,
            method="CARRIER",
            tracking_number=tracking_number,
            tracking_url=tracking_url,
            status="DISPATCHED",
        )
        session.add(handover)
    else:
        handover.tracking_number = tracking_number
        handover.tracking_url = tracking_url
        handover.status = "DISPATCHED"

    lot_code = "???"
    if deal.lot_id:
        lot = session.get(Lot, deal.lot_id)
        if lot:
            lot_code = lot.code

    tmpl_content = TRACKING_TEMPLATE.read_text(encoding="utf-8")
    context = {
        "lot_code": lot_code,
        "carrier": carrier,
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
    }
    rendered_message = render_outgoing_template(tmpl_content, context)

    record_truth(
        session=session,
        claim=f"Трек-номер {tracking_number} ({carrier}) выдан для сделки #{deal.id}.",
        recipient="OWNER",
        evidence={"deal_id": deal.id, "carrier": carrier, "tracking_number": tracking_number},
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="TRACKING_NUMBER_RELEASED",
        entity="deal",
        entity_id=deal.id,
        data={"lot_code": lot_code, "carrier": carrier, "tracking_number": tracking_number},
    )

    session.flush()

    return {
        "deal_id": deal.id,
        "lot_code": lot_code,
        "carrier": carrier,
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
        "message": rendered_message,
        "storage_location": _storage_location_for_deal(session, deal),
    }
