"""Escalation management service with translation and owner command control (TZ v2.0 Section V.4).

DoD:
- Package incoming message with original text, Russian translation, and prepared draft response.
- Formats owner notification with mandatory [lot_code] prefix.
- Prepared response strictly NEVER dispatches to the counterparty without an explicit owner command.
- Owner commands: SEND (approve prepared), EDIT (custom response), DECLINE (dismiss).
- Full audit and truth assertions recorded.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.comms.translation import translate_text
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Listing, Lot, Message, Thread

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "templates"
    / "comms"
    / "owner_escalation.jinja2"
)


class EscalationError(NextLifeError):
    """Base error for escalation operations."""


def create_owner_escalation(
    session: Session,
    message_id: str,
    *,
    reason: str = "manual_decision_required",
    prepared_response: str | None = None,
    actor: str = "AGENT",
) -> dict[str, Any]:
    """Package message escalation for owner review and await owner command.

    DoD:
    - Prepared response is preserved in draft state and NOT dispatched to counterparty.
    - Owner notification formatted with mandatory [lot_code] prefix.
    """
    msg = session.get(Message, message_id)
    if not msg:
        raise EscalationError(f"Message '{message_id}' not found")

    thread = session.get(Thread, msg.thread_id)
    if not thread:
        raise EscalationError(f"Thread '{msg.thread_id}' not found")

    # Resolve lot code
    item_code = None
    lot_code = "???"
    if thread.listing_id:
        listing = session.get(Listing, thread.listing_id)
        if listing:
            item_code = listing.item_code

    if item_code:
        lot = session.scalar(select(Lot).where(Lot.item_id == item_code))
        if lot:
            lot_code = lot.code
        else:
            lot_code = item_code

    # Translate to Russian for owner if not already present
    if not msg.text_ru:
        msg.text_ru = translate_text(msg.text_original, source_lang=msg.lang_original, target_lang="ru")

    # Default prepared response in buyer's language if none provided
    if not prepared_response:
        default_ru = "Здравствуйте. Уточню этот вопрос и вернусь с ответом."
        prepared_response = translate_text(default_ru, source_lang="ru", target_lang=msg.lang_original)

    # Store escalation state on message
    ev = dict(msg.source_evidence or {})
    ev["escalation_status"] = "AWAITING_OWNER_COMMAND"
    ev["escalation_reason"] = reason
    ev["prepared_response"] = prepared_response
    ev["lot_code"] = lot_code
    msg.source_evidence = ev
    msg.escalated = True
    msg.answered_by = None  # Explicitly not answered yet!
    session.flush()

    # Format owner notification via Jinja2 template
    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")
    context = {
        "lot_code": lot_code,
        "sender": thread.counterparty,
        "reason": reason,
        "lang": msg.lang_original,
        "original_text": msg.text_original,
        "translated_text_ru": msg.text_ru,
        "prepared_response": prepared_response,
    }
    check_context_for_internal_fields(context)
    owner_notification_text = render_outgoing_template(template_str, context)

    # Enqueue notification to OWNER (Telegram)
    enqueue_outbox(
        session=session,
        transport="TELEGRAM",
        body=owner_notification_text,
        idempotency_key=f"owner-notify-{msg.id}",
        recipient_hint="OWNER",
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="MESSAGE_ESCALATED",
        entity="message",
        entity_id=msg.id,
        data={
            "lot_code": lot_code,
            "reason": reason,
            "counterparty": thread.counterparty,
            "awaiting_command": True,
        },
    )

    session.flush()

    return {
        "message_id": msg.id,
        "lot_code": lot_code,
        "status": "AWAITING_OWNER_COMMAND",
        "prepared_response": prepared_response,
        "owner_notification": owner_notification_text,
    }


def execute_owner_command(
    session: Session,
    message_id: str,
    command: str,
    *,
    custom_text: str | None = None,
    actor: str = "OWNER",
) -> dict[str, Any]:
    """Execute explicit owner command on an escalated message.

    Commands:
    - SEND / APPROVE: Dispatches the prepared response to the buyer.
    - EDIT: Dispatches custom_text to the buyer.
    - DECLINE / DISMISS: Closes the escalation without sending any message.
    """
    msg = session.get(Message, message_id)
    if not msg:
        raise EscalationError(f"Message '{message_id}' not found")

    thread = session.get(Thread, msg.thread_id)
    if not thread:
        raise EscalationError(f"Thread '{msg.thread_id}' not found")

    cmd = command.strip().upper()
    now_utc = datetime.now(UTC)
    ev = dict(msg.source_evidence or {})

    if cmd in ("SEND", "APPROVE"):
        text_to_send = custom_text or ev.get("prepared_response")
        if not text_to_send:
            raise EscalationError("No prepared response found to send.")

        # Create outbound message in thread
        out_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            direction="OUT",
            transport=msg.transport,
            text_original=text_to_send,
            lang_original=msg.lang_original,
            sent_at=now_utc,
            answered_by="OWNER",
            source_evidence={"approved_for_message_id": msg.id},
        )
        session.add(out_msg)

        # Enqueue into outbox for actual dispatch to buyer
        enqueue_outbox(
            session=session,
            transport=msg.transport,
            body=text_to_send,
            idempotency_key=f"reply-{msg.id}-{uuid.uuid4().hex[:6]}",
            recipient_hint=thread.counterparty,
        )

        msg.answered_by = "OWNER"
        ev["escalation_status"] = "SENT"
        msg.source_evidence = ev
        session.flush()

        record_truth(
            session=session,
            claim=f"Ответ на обращение #{msg.id} отправлен покупателю {thread.counterparty} по явной команде владельца {cmd}.",
            recipient="OWNER",
            evidence={"message_id": msg.id, "sent_text": text_to_send},
        )
        record_audit_event(
            session=session,
            actor=actor,
            action="ESCALATION_COMMAND_EXECUTED",
            entity="message",
            entity_id=msg.id,
            data={"command": cmd, "action": "REPLY_SENT", "text": text_to_send},
        )

        return {
            "message_id": msg.id,
            "command": cmd,
            "status": "SENT",
            "sent_text": text_to_send,
        }

    elif cmd == "EDIT":
        if not custom_text or not custom_text.strip():
            raise EscalationError("EDIT command requires non-empty custom_text.")

        out_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            direction="OUT",
            transport=msg.transport,
            text_original=custom_text.strip(),
            lang_original=msg.lang_original,
            sent_at=now_utc,
            answered_by="OWNER",
            source_evidence={"edited_from_message_id": msg.id},
        )
        session.add(out_msg)

        enqueue_outbox(
            session=session,
            transport=msg.transport,
            body=custom_text.strip(),
            idempotency_key=f"reply-edit-{msg.id}-{uuid.uuid4().hex[:6]}",
            recipient_hint=thread.counterparty,
        )

        msg.answered_by = "OWNER"
        ev["escalation_status"] = "EDITED_AND_SENT"
        msg.source_evidence = ev
        session.flush()

        record_audit_event(
            session=session,
            actor=actor,
            action="ESCALATION_COMMAND_EXECUTED",
            entity="message",
            entity_id=msg.id,
            data={"command": cmd, "action": "REPLY_EDITED_AND_SENT", "text": custom_text.strip()},
        )

        return {
            "message_id": msg.id,
            "command": cmd,
            "status": "EDITED_AND_SENT",
            "sent_text": custom_text.strip(),
        }

    elif cmd in ("DECLINE", "DISMISS"):
        msg.answered_by = "OWNER"
        ev["escalation_status"] = "DECLINED"
        msg.source_evidence = ev
        session.flush()

        record_audit_event(
            session=session,
            actor=actor,
            action="ESCALATION_COMMAND_EXECUTED",
            entity="message",
            entity_id=msg.id,
            data={"command": cmd, "action": "DISMISSED"},
        )

        return {
            "message_id": msg.id,
            "command": cmd,
            "status": "DISMISSED",
            "sent_text": None,
        }

    else:
        raise EscalationError(f"Unknown owner command: '{command}'. Allowed: SEND, EDIT, DECLINE.")


def get_escalation_details(session: Session, message_id: str) -> dict[str, Any]:
    """Retrieve full details of an escalated message for owner inspection."""
    msg = session.get(Message, message_id)
    if not msg:
        raise EscalationError(f"Message '{message_id}' not found")

    thread = session.get(Thread, msg.thread_id)
    ev = msg.source_evidence or {}

    return {
        "message_id": msg.id,
        "thread_id": msg.thread_id,
        "lot_code": ev.get("lot_code", "???"),
        "counterparty": thread.counterparty if thread else "unknown",
        "original_text": msg.text_original,
        "translated_text_ru": msg.text_ru,
        "prepared_response": ev.get("prepared_response"),
        "status": ev.get("escalation_status", "PENDING"),
        "escalated": msg.escalated,
        "answered_by": msg.answered_by,
    }
