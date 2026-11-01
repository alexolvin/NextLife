"""Outbox service for OpenClaw communication decoupling (TZ Section 4.4, Section 5)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nextlife.core.exceptions import InputError
from nextlife.storage.models import Outbox


class OutboxError(InputError):
    """Error related to outbox operations."""


def enqueue_outbox(
    session: Session,
    transport: str,
    body: str,
    idempotency_key: str,
    recipient_hint: str | None = None,
    subject: str | None = None,
    attachments: list[Any] | None = None,
) -> Outbox:
    """Queue a message to outbox for OpenClaw delivery."""
    existing = session.execute(
        select(Outbox).where(Outbox.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing:
        return existing

    outbox_msg = Outbox(
        id=str(uuid.uuid4()),
        transport=transport,
        recipient_hint=recipient_hint,
        subject=subject,
        body=body,
        attachments=attachments or [],
        idempotency_key=idempotency_key,
        state="PENDING",
        attempts=0,
        delivery_evidence={},
    )
    session.add(outbox_msg)
    session.flush()
    return outbox_msg


def pull_outbox(
    session: Session,
    limit: int = 50,
    retry_timeout_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Pull pending or unacknowledged messages for OpenClaw delivery."""
    cutoff = datetime.now(UTC) - timedelta(seconds=retry_timeout_seconds)
    query = (
        select(Outbox)
        .where(
            or_(
                Outbox.state == "PENDING",
                (Outbox.state == "PULLED") & (Outbox.pulled_at < cutoff),
            )
        )
        .order_by(Outbox.created_at.asc())
        .limit(limit)
    )

    items = list(session.execute(query).scalars().all())
    now = datetime.now(UTC)
    result = []
    for item in items:
        item.state = "PULLED"
        item.pulled_at = now
        item.attempts += 1
        result.append(
            {
                "id": item.id,
                "transport": item.transport,
                "recipient_hint": item.recipient_hint,
                "subject": item.subject,
                "body": item.body,
                "attachments": item.attachments,
                "idempotency_key": item.idempotency_key,
                "attempts": item.attempts,
            }
        )
    session.flush()
    return result


def ack_outbox(
    session: Session,
    outbox_id: str,
    delivery_evidence: dict[str, Any],
) -> Outbox:
    """Acknowledge delivery with mandatory non-empty delivery evidence."""
    if (
        not delivery_evidence
        or not isinstance(delivery_evidence, dict)
        or len(delivery_evidence) == 0
    ):
        raise OutboxError("delivery_evidence must be a non-empty dictionary/JSON object")

    item = session.execute(select(Outbox).where(Outbox.id == outbox_id)).scalar_one_or_none()

    if not item:
        raise OutboxError(f"Outbox entry not found: {outbox_id}")

    item.state = "SENT"
    item.acked_at = datetime.now(UTC)
    item.delivery_evidence = delivery_evidence
    session.flush()
    return item
