"""Inbox service for receiving external messages via OpenClaw (Section 4.4, Section 17)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.comms.classifier import process_incoming_message
from nextlife.comms.outbox import enqueue_outbox
from nextlife.storage.models import Listing, Message, Thread


def handle_inbox_push(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Process an incoming email message pushed by OpenClaw.

    Deduplicates strictly by external message_id (Section 17).
    """
    message_id = payload.get("message_id") or payload.get("Message-ID") or ""
    sender = payload.get("from") or payload.get("sender") or ""
    subject = payload.get("subject") or ""
    body = payload.get("body") or payload.get("text") or ""

    if not message_id:
        message_id = f"gen-{uuid.uuid4()}"

    # Check for deduplication by Message-ID (Section 17)
    existing_msg = session.execute(
        select(Message).where(Message.external_message_id == message_id)
    ).scalar_one_or_none()

    if existing_msg:
        return {
            "status": "duplicate",
            "message_id": message_id,
            "id": existing_msg.id,
            "thread_id": existing_msg.thread_id,
        }

    # Find or create thread
    # First classify / process message
    classification = process_incoming_message(
        session=session,
        sender=sender,
        subject=subject,
        body=body,
        channel="EMAIL",
    )

    item_code = classification.get("item_code")

    # Find listing
    listing_id = None
    if item_code:
        listing = (
            session.execute(select(Listing).where(Listing.item_code == item_code)).scalars().first()
        )
        if listing:
            listing_id = listing.id

    thread = None
    if listing_id:
        thread = session.execute(
            select(Thread).where(
                Thread.listing_id == listing_id,
                Thread.counterparty == sender,
            )
        ).scalar_one_or_none()

    if not thread:
        thread = Thread(
            id=str(uuid.uuid4()),
            listing_id=listing_id,
            channel="EMAIL",
            counterparty=sender,
            lang=classification.get("lang", "en"),
            status="ACTIVE",
        )
        session.add(thread)
        session.flush()

    # Create incoming Message record
    in_msg = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        direction="IN",
        transport="EMAIL",
        external_message_id=message_id,
        text_original=body,
        lang_original=classification.get("lang", "en"),
        escalated=(classification.get("type") == "ESCALATION"),
        source_evidence={
            "payload_headers": {k: v for k, v in payload.items() if k not in ("body", "text")}
        },
    )
    session.add(in_msg)
    session.flush()

    # Route according to classification
    if classification.get("type") == "AUTO_REPLY":
        reply_text = classification.get("reply_text", "")
        # Create OUT message in DB
        out_msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            direction="OUT",
            transport="EMAIL",
            text_original=reply_text,
            lang_original=classification.get("lang", "en"),
            answered_by="AGENT",
            template_key=classification.get("template_key"),
            source_evidence=classification.get("evidence", {}),
        )
        session.add(out_msg)

        # Enqueue reply into outbox
        enqueue_outbox(
            session=session,
            transport="EMAIL",
            recipient_hint=sender,
            subject=f"Re: {subject}",
            body=reply_text,
            idempotency_key=f"reply-{message_id}",
        )
        session.commit()
        return {
            "status": "auto_replied",
            "message_id": message_id,
            "thread_id": thread.id,
            "item_code": item_code,
        }

    else:
        # Escalation to owner via Telegram outbox
        reason = classification.get("reason", "manual_review")
        prefix = f"[{item_code}]: " if item_code else "[NL-???]: "
        enqueue_outbox(
            session=session,
            transport="TELEGRAM",
            body=f"{prefix}Требуется внимание Владельца к сообщению от {sender}: {reason}",
            idempotency_key=f"escalate-{message_id}",
            recipient_hint="OWNER",
        )
        session.commit()
        return {
            "status": "escalated",
            "message_id": message_id,
            "thread_id": thread.id,
            "item_code": item_code,
            "reason": reason,
        }


def get_pending_inbox(session: Session) -> list[dict[str, Any]]:
    """List pending messages requiring owner decision."""
    query = (
        select(Message)
        .where(Message.escalated.is_(True), Message.answered_by.is_(None))
        .order_by(Message.sent_at.desc())
    )
    messages = session.execute(query).scalars().all()
    results = []
    for msg in messages:
        results.append(
            {
                "id": msg.id,
                "thread_id": msg.thread_id,
                "sent_at": msg.sent_at.isoformat(),
                "lang": msg.lang_original,
                "text_original": msg.text_original,
            }
        )
    return results
