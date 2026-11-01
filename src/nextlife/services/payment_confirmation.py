"""Payment confirmation service and proof enforcement (TZ v2.0 Section III.3.3).

DoD:
- Transition to PAID is permitted ONLY by OWNER or by MARKETPLACE with proof.
- AGENT is strictly forbidden from transitioning to PAID.
- Counterparty payment screenshot is strictly NOT considered proof of payment and creates an escalation.
- Agent never tells counterparty "payment received" until state is PAID.
- Module length <= 400 lines.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.services.escalation import create_owner_escalation
from nextlife.storage.models import Message, Thread
from nextlife.storage.models.deals import Deal
from nextlife.storage.models.lots import Lot


class PaymentConfirmationError(NextLifeError):
    """Base error for payment confirmation failures."""


class UnauthorizedPaymentConfirmationError(PaymentConfirmationError):
    """Raised when an unauthorized actor (e.g. AGENT) attempts to confirm payment."""


class PaymentScreenshotNotProofError(PaymentConfirmationError):
    """Raised when a screenshot is falsely presented as payment proof."""


def confirm_payment(
    session: Session,
    deal_id: str,
    *,
    actor: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transition deal to PAID with strict actor and evidence validation.

    DoD:
    - Transition to PAID allowed ONLY for OWNER or MARKETPLACE with verifiable proof.
    - AGENT is strictly forbidden from initiating PAID.
    """
    deal = session.get(Deal, deal_id)
    if not deal:
        raise PaymentConfirmationError(f"Deal '{deal_id}' not found")

    actor_upper = actor.strip().upper()

    # Strict rule: AGENT cannot transition to PAID
    if actor_upper == "AGENT":
        raise UnauthorizedPaymentConfirmationError(
            "Agent is strictly prohibited from confirming payments (DoD rule III.3.3). "
            "Payment can only be confirmed by OWNER or MARKETPLACE proof."
        )

    if actor_upper == "MARKETPLACE":
        ev = evidence or {}
        if not ev or ev.get("payment_status") != "PAID":
            raise PaymentConfirmationError(
                "Marketplace payment confirmation requires verifiable evidence with payment_status='PAID'."
            )
    elif actor_upper != "OWNER":
        raise UnauthorizedPaymentConfirmationError(f"Unknown or unauthorized actor for payment confirmation: '{actor}'")

    # Update deal status
    now_utc = datetime.now(UTC)
    deal.status = "PAID"
    ev_dict = dict(deal.closure_evidence or {})
    ev_dict["payment_confirmed_by"] = actor_upper
    ev_dict["payment_confirmed_at"] = now_utc.isoformat()
    ev_dict["payment_evidence"] = evidence or {}
    deal.closure_evidence = ev_dict

    # If lot is associated, keep lot status aligned
    if deal.lot_id:
        lot = session.get(Lot, deal.lot_id)
        if lot:
            lot.status = "PAID"

    session.flush()

    record_truth(
        session=session,
        claim=f"Оплата по сделке #{deal.id} в сумме {deal.agreed_price} {deal.currency} подтверждена инициатором {actor_upper}.",
        recipient="OWNER",
        evidence={"actor": actor_upper, "evidence": evidence},
    )

    record_audit_event(
        session=session,
        actor=actor_upper,
        action="PAYMENT_CONFIRMED",
        entity="deal",
        entity_id=deal.id,
        data={"agreed_price": str(deal.agreed_price), "currency": deal.currency, "evidence": evidence},
    )

    session.flush()

    return {
        "deal_id": deal.id,
        "status": "PAID",
        "confirmed_by": actor_upper,
        "confirmed_at": now_utc.isoformat(),
    }


def handle_buyer_payment_notification(
    session: Session,
    deal_id: str,
    message_id: str,
    *,
    has_screenshot: bool = False,
    attachment_name: str | None = None,
) -> dict[str, Any]:
    """Handle counterparty claim of payment or incoming payment screenshot.

    DoD:
    - Counterparty screenshot is strictly NOT proof of payment.
    - Does NOT transition deal to PAID.
    - Escalates message to OWNER for manual bank account verification.
    - Draft response promises verification, never stating 'payment received'.
    """
    deal = session.get(Deal, deal_id)
    if not deal:
        raise PaymentConfirmationError(f"Deal '{deal_id}' not found")

    reason = (
        "buyer_payment_screenshot_unverified"
        if has_screenshot
        else "buyer_payment_claim_unverified"
    )

    # Safe draft response that strictly does NOT confirm receipt of funds
    safe_draft = (
        "Dobrý den, děkuji za zprávu. "
        "Platbu ověřím na bankovním účtu a jakmile bude připsána, potvrdím odeslání."
    )

    # Ensure message and thread exist for escalation packaging
    msg = session.get(Message, message_id)
    if not msg:
        thread_id = f"thread-deal-{deal_id}"
        thread = session.get(Thread, thread_id)
        if not thread:
            thread = Thread(
                id=thread_id,
                listing_id=deal.listing_id,
                channel="WEB_UI",
                counterparty="buyer",
                lang="cs",
                status="ACTIVE",
            )
            session.add(thread)
            session.flush()

        msg = Message(
            id=message_id,
            thread_id=thread.id,
            direction="IN",
            transport="WEB_UI",
            text_original=f"Platba za zakázku {deal_id}. Příloha: {attachment_name or 'screenshot.png'}",
            lang_original="cs",
            sent_at=datetime.now(UTC),
        )
        session.add(msg)
        session.flush()

    # Escalate to OWNER
    escalation_res = create_owner_escalation(
        session=session,
        message_id=message_id,
        reason=reason,
        prepared_response=safe_draft,
        actor="AGENT",
    )

    # Audit that screenshot was rejected as proof
    record_audit_event(
        session=session,
        actor="SYSTEM",
        action="PAYMENT_SCREENSHOT_REJECTED_AS_PROOF",
        entity="deal",
        entity_id=deal.id,
        data={
            "message_id": message_id,
            "has_screenshot": has_screenshot,
            "attachment_name": attachment_name,
            "deal_status": deal.status,
        },
    )

    session.flush()

    escalation_id = None
    if isinstance(escalation_res, dict):
        escalation_id = escalation_res.get("message_id") or escalation_res.get("id")
    elif hasattr(escalation_res, "id"):
        escalation_id = str(escalation_res.id)
    if not escalation_id:
        escalation_id = str(message_id)

    return {
        "deal_id": deal.id,
        "deal_status": deal.status,  # Remains unchanged (e.g. RESERVED / AWAITING_PAYMENT)
        "status": "escalated",
        "escalation_id": escalation_id,
        "paid": False,
        "escalated_to_owner": True,
        "escalation": escalation_res,
    }
