"""Dispute management and dossier assembly service (TZ v2.0 Section III.7 / Task T-068).

DoD:
- Full dossier assembly: chronology, listings snapshot, dispatch evidence, handover act, photos.
- Neutral counterparty auto-acknowledgment strictly without admission of fault or promises.
- Prohibition of promises/concessions enforced by forbidden expressions validator.
- Settlement funds held, lot deletion blocked.
- Context checked by outgoing filter.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.deals.handover_protocol import get_handover_protocol
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.services.dispatch_evidence import get_evidence_status
from nextlife.storage.models import (
    Deal,
    Item,
    Lot,
    Message,
    Settlement,
    Thread,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
COMMS_DIR = BASE_DIR / "templates" / "comms"
NEUTRAL_ACK_TEMPLATE = COMMS_DIR / "dispute_neutral_acknowledgment.jinja2"
OWNER_OPTIONS_TEMPLATE = COMMS_DIR / "dispute_owner_options.jinja2"

FORBIDDEN_DISPUTE_EXPRESSIONS = [
    # Russian
    "виноваты",
    "признаем",
    "признаём",
    "компенсируем",
    "вернем деньги",
    "вернём деньги",
    "наша ошибка",
    "бракованный",
    "возместим",
    "обещаем",
    "гарантируем возврат",
    "извините за неисправность",
    # Czech
    "uznáváme",
    "naše chyba",
    "vrátíme peníze",
    "kompenzujeme",
    "vadný kus",
    "slibujeme",
    "garantujeme vrácení",
    "omlouváme se za vadu",
    # English
    "we admit",
    "our fault",
    "refund guaranteed",
    "we promise",
    "defective unit",
]


class DisputePromiseForbiddenError(NextLifeError):
    """Raised when an auto-reply or message contains forbidden concessions or promises."""


class DisputeStateError(NextLifeError):
    """Raised when dispute transition or operation is invalid."""


def validate_neutral_dispute_response(text: str) -> None:
    """Scan response text against list of forbidden promise and concession expressions."""
    lower = text.lower()
    for phrase in FORBIDDEN_DISPUTE_EXPRESSIONS:
        if phrase in lower:
            raise DisputePromiseForbiddenError(
                f"Forbidden concession/promise expression detected in dispute response: '{phrase}'"
            )


def render_neutral_acknowledgment(lot_code: str, lang: str = "cs") -> str:
    """Render neutral acknowledgment response without liability or promises."""
    context = {"lot_code": lot_code, "lang": lang}
    check_context_for_internal_fields(context)

    if not NEUTRAL_ACK_TEMPLATE.exists():
        raise DisputeStateError(f"Template missing: {NEUTRAL_ACK_TEMPLATE}")

    template_str = NEUTRAL_ACK_TEMPLATE.read_text(encoding="utf-8")
    rendered = render_outgoing_template(template_str, context)

    # Strict compliance check
    validate_neutral_dispute_response(rendered)
    return rendered


def assemble_dispute_dossier(session: Session, deal_id: str) -> dict[str, Any]:
    """Assemble complete dispute case dossier with full timestamped chronology."""
    deal = session.get(Deal, deal_id)
    if not deal:
        raise DisputeStateError(f"Deal '{deal_id}' not found")

    lot = session.get(Lot, deal.lot_id) if deal.lot_id else None
    lot_code = lot.code if lot else "N/A"

    item = session.get(Item, lot.item_id) if (lot and lot.item_id) else None

    # Chronology of messages
    chronology = []
    threads = session.query(Thread).filter(Thread.listing_id == deal.listing_id).all() if deal.listing_id else []
    for th in threads:
        msgs = session.query(Message).filter(Message.thread_id == th.id).order_by(Message.sent_at.asc()).all()
        for m in msgs:
            chronology.append({
                "timestamp": m.sent_at.isoformat() if m.sent_at else None,
                "direction": m.direction,
                "text": m.text_original,
                "lang": m.lang_original,
                "escalated": m.escalated,
            })

    # Evidence details
    ev_status = get_evidence_status(session, deal.id)
    protocol_text = get_handover_protocol(session, deal.id)

    return {
        "deal_id": deal.id,
        "lot_code": lot_code,
        "item_title": item.title if item else "N/A",
        "item_category": item.category_key if item else "N/A",
        "agreed_price": str(deal.agreed_price),
        "currency": deal.currency,
        "payment_method": deal.payment_method,
        "delivery_method": deal.delivery_method,
        "chronology": chronology,
        "dispatch_evidence": ev_status,
        "has_handover_act": protocol_text is not None,
        "assembled_at": datetime.now(UTC).isoformat(),
    }


def open_dispute(
    session: Session,
    deal_id: str,
    reason: str,
    thread_id: str | None = None,
    actor: str = "BUYER",
) -> dict[str, Any]:
    """Open dispute case, lock payout, assemble dossier, dispatch neutral reply."""
    deal = session.get(Deal, deal_id)
    if not deal:
        raise DisputeStateError(f"Deal '{deal_id}' not found")

    # Update deal status to DISPUTED
    deal.status = "DISPUTED"

    # Lock settlement payout
    settlement = session.query(Settlement).filter(Settlement.deal_id == deal.id).first()
    if settlement:
        settlement.payout_status = "HELD"

    lot = session.get(Lot, deal.lot_id) if deal.lot_id else None
    lot_code = lot.code if lot else "N/A"

    # Assemble dossier
    dossier = assemble_dispute_dossier(session, deal.id)

    # Send neutral acknowledgment to buyer
    lang = "cs"
    if thread_id:
        th = session.get(Thread, thread_id)
        if th and th.lang:
            lang = th.lang

    ack_text = render_neutral_acknowledgment(lot_code=lot_code, lang=lang)

    buyer_msg = None
    if thread_id:
        buyer_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            direction="OUT",
            transport="MARKETPLACE",
            text_original=ack_text,
            lang_original=lang,
            sent_at=datetime.now(UTC),
            template_key="dispute_neutral_ack",
        )
        session.add(buyer_msg)

    # Format owner options
    has_act = dossier["has_handover_act"]
    owner_opts_context = {
        "lot_code": lot_code,
        "amount": deal.agreed_price,
        "currency": deal.currency,
        "messages_count": len(dossier["chronology"]),
        "evidences_count": len(dossier["dispatch_evidence"]["present"]),
        "has_protocol": has_act,
    }
    check_context_for_internal_fields(owner_opts_context)

    owner_text = render_outgoing_template(
        OWNER_OPTIONS_TEMPLATE.read_text(encoding="utf-8"),
        owner_opts_context,
    )

    owner_outbox = enqueue_outbox(
        session=session,
        transport="TELEGRAM",
        recipient_hint="OWNER",
        body=owner_text,
        idempotency_key=f"dispute-owner-{deal.id}-{uuid.uuid4().hex[:6]}",
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="DISPUTE_OPENED",
        entity="deal",
        entity_id=deal.id,
        data={
            "reason": reason,
            "payout_status": "HELD",
            "dossier_chronology_len": len(dossier["chronology"]),
        },
    )
    record_truth(
        session=session,
        claim="DISPUTE_OPENED",
        recipient="OWNER",
        evidence={
            "deal_id": deal.id,
            "lot_code": lot_code,
            "reason": reason,
            "neutral_reply_dispatched": True,
        },
    )
    session.flush()

    return {
        "deal_id": deal.id,
        "status": deal.status,
        "neutral_acknowledgment": ack_text,
        "owner_notification_id": owner_outbox.id,
        "dossier": dossier,
    }
