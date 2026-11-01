"""Dobírka (Cash on Delivery) risk calculation and uncollected return service (TZ v2.0 Section III.3.4).

DoD:
- Owner receives calculation of maximum loss before dispatch.
- When buyer fails to collect parcel, deal transitions to RETURNED.
- Expenses (forward delivery, return delivery, COD fee, packaging) are recorded in settlement.
- Truth assertion and audit event logged.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.outgoing_filter import render_outgoing_template
from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ItemStatus
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models.deals import Deal, Party, Settlement
from nextlife.storage.models.lots import Lot

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "templates"
    / "comms"
    / "cod_risk_warning.jinja2"
)


class DobirkaError(NextLifeError):
    """Base error for Dobírka operations."""


def calculate_cod_risk(
    forward_shipping: Decimal,
    *,
    return_shipping: Decimal | None = None,
    cod_fee: Decimal = Decimal("25.00"),
    packaging_cost: Decimal = Decimal("35.00"),
) -> dict[str, Decimal]:
    """Calculate potential loss breakdown if parcel is not collected by buyer."""
    return_cost = return_shipping if return_shipping is not None else forward_shipping
    total_loss = forward_shipping + return_cost + cod_fee + packaging_cost
    return {
        "forward_shipping": forward_shipping,
        "return_shipping": return_cost,
        "cod_fee": cod_fee,
        "packaging_cost": packaging_cost,
        "total_risk_loss": total_loss,
    }


def generate_cod_pre_dispatch_warning(
    session: Session,
    deal_id: str,
    *,
    forward_shipping: Decimal,
    return_shipping: Decimal | None = None,
    cod_fee: Decimal = Decimal("25.00"),
    packaging_cost: Decimal = Decimal("35.00"),
    notify_owner: bool = True,
) -> dict[str, Any]:
    """Provide owner with loss calculation before sending parcel via Dobírka.

    DoD:
    - Owner receives risk calculation before dispatch.
    - Loss breakdown recorded on deal.
    """
    deal = session.get(Deal, deal_id)
    if not deal:
        raise DobirkaError(f"Deal '{deal_id}' not found")

    pay_method = (deal.payment_method or "").upper()
    if pay_method not in ("COD", "DOBIRKA"):
        raise DobirkaError(f"Deal '{deal_id}' is not configured for COD (payment_method: '{deal.payment_method}')")

    risk = calculate_cod_risk(
        forward_shipping=forward_shipping,
        return_shipping=return_shipping,
        cod_fee=cod_fee,
        packaging_cost=packaging_cost,
    )

    lot_code = "???"
    if deal.lot_id:
        lot = session.get(Lot, deal.lot_id)
        if lot:
            lot_code = lot.code

    counterparty = "Покупатель"
    if deal.party_id:
        party = session.get(Party, deal.party_id)
        if party and party.name:
            counterparty = party.name

    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")
    context = {
        "lot_code": lot_code,
        "counterparty": counterparty,
        "currency": deal.currency,
        "lot_price": str(deal.agreed_price),
        "forward_shipping": str(risk["forward_shipping"]),
        "return_shipping": str(risk["return_shipping"]),
        "cod_fee": str(risk["cod_fee"]),
        "packaging_cost": str(risk["packaging_cost"]),
        "total_risk_loss": str(risk["total_risk_loss"]),
    }
    warning_text = render_outgoing_template(template_str, context)

    # Save risk calculation on deal
    ev = dict(deal.closure_evidence or {})
    ev["cod_risk_calculation"] = {k: str(v) for k, v in risk.items()}
    deal.closure_evidence = ev
    session.flush()

    if notify_owner:
        enqueue_outbox(
            session=session,
            transport="TELEGRAM",
            body=warning_text,
            idempotency_key=f"cod-warning-{deal.id}",
            recipient_hint="OWNER",
        )

    record_audit_event(
        session=session,
        actor="SYSTEM",
        action="COD_DISPATCH_RISK_CALCULATED",
        entity="deal",
        entity_id=deal.id,
        data={
            "total_risk_loss": str(risk["total_risk_loss"]),
            "forward_shipping": str(risk["forward_shipping"]),
            "return_shipping": str(risk["return_shipping"]),
        },
    )
    session.flush()

    return {
        "deal_id": deal.id,
        "risk_breakdown": risk,
        "warning_text": warning_text,
    }


def handle_cod_uncollected(
    session: Session,
    deal_id: str,
    *,
    return_shipping: Decimal | None = None,
    restore_lot_to_published: bool = False,
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Handle uncollected COD shipment: transition to RETURNED and fix expenses in settlement.

    DoD:
    - Transition deal to RETURNED.
    - Record delivery, return, COD fee, and packaging costs in settlement.
    - Update lot status to RETURNED (or PUBLISHED if owner decided to relist).
    """
    deal = session.get(Deal, deal_id)
    if not deal:
        raise DobirkaError(f"Deal '{deal_id}' not found")

    ev = dict(deal.closure_evidence or {})
    risk_info = ev.get("cod_risk_calculation", {})

    forward_shipping = Decimal(risk_info.get("forward_shipping", "89.00"))
    cod_fee = Decimal(risk_info.get("cod_fee", "25.00"))
    packaging_cost = Decimal(risk_info.get("packaging_cost", "35.00"))
    actual_return_shipping = return_shipping if return_shipping is not None else Decimal(risk_info.get("return_shipping", str(forward_shipping)))

    total_shipping_loss = forward_shipping + actual_return_shipping + cod_fee
    total_net_loss = total_shipping_loss + packaging_cost

    now_utc = datetime.now(UTC)
    deal.status = "RETURNED"
    deal.closed_at = now_utc
    ev["return_reason"] = "COD_UNCOLLECTED_BY_BUYER"
    deal.closure_evidence = ev

    # Update handover status
    if deal.handover:
        deal.handover.status = "RETURNED"

    # Create or update Settlement fixing the incurred expenses
    settlement = deal.settlement
    if not settlement:
        settlement = Settlement(
            id=str(uuid.uuid4()),
            deal_id=deal.id,
            gross_amount=Decimal("0.00"),
            platform_fee=Decimal("0.00"),
            delivery_cost=total_shipping_loss,
            packaging_cost=packaging_cost,
            net_payout=-total_net_loss,
            currency=deal.currency,
            payout_status="SETTLED",
            settled_at=now_utc,
        )
        session.add(settlement)
    else:
        settlement.gross_amount = Decimal("0.00")
        settlement.platform_fee = Decimal("0.00")
        settlement.delivery_cost = total_shipping_loss
        settlement.packaging_cost = packaging_cost
        settlement.net_payout = -total_net_loss
        settlement.payout_status = "SETTLED"
        settlement.settled_at = now_utc

    # Update Lot status
    lot = session.get(Lot, deal.lot_id) if deal.lot_id else None
    if lot:
        if restore_lot_to_published:
            lot.status = ItemStatus.PUBLISHED.value
            lot.reserved_for_party_id = None
            lot.reserved_until = None
        else:
            lot.status = ItemStatus.RETURNED.value
            lot.reserved_for_party_id = None

    session.flush()

    record_truth(
        session=session,
        claim=f"Сделка #{deal.id} переведена в RETURNED (невыкуп dobírka). Зафиксирован убыток на доставку и возврат: {total_net_loss} {deal.currency}.",
        recipient="OWNER",
        evidence={
            "deal_id": deal.id,
            "status": "RETURNED",
            "total_net_loss": str(total_net_loss),
            "settlement_id": settlement.id,
        },
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="COD_RETURNED_RECORDED",
        entity="deal",
        entity_id=deal.id,
        data={
            "deal_status": "RETURNED",
            "delivery_cost": str(total_shipping_loss),
            "packaging_cost": str(packaging_cost),
            "net_payout": str(settlement.net_payout),
            "restored_to_published": restore_lot_to_published,
        },
    )

    session.flush()

    return {
        "deal_id": deal.id,
        "status": "RETURNED",
        "total_shipping_loss": total_shipping_loss,
        "packaging_cost": packaging_cost,
        "net_loss": total_net_loss,
        "settlement_net_payout": settlement.net_payout,
        "lot_status": lot.status if lot else None,
    }
