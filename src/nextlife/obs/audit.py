"""Audit log recorder."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from nextlife.storage.models import AuditEvent


def record_audit_event(
    session: Session,
    actor: str,
    action: str,
    entity: str,
    entity_id: str,
    data: dict[str, Any],
) -> AuditEvent:
    event = AuditEvent(
        at=datetime.now(UTC),
        actor=actor,
        action=action,
        entity=entity,
        entity_id=entity_id,
        data=data,
    )
    session.add(event)
    return event
