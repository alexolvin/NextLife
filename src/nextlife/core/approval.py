"""Draft approval and canonical payload hashing."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models import Approval


class ApprovalError(NextLifeError):
    """Raised on approval validation failure."""


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of canonical JSON representation."""
    canonical_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def record_approval(
    session: Session,
    item_code: str,
    payload: dict[str, Any],
    provided_hash: str,
    channel: str,
) -> Approval:
    expected_hash = compute_payload_hash(payload)
    if provided_hash != expected_hash:
        raise ApprovalError(
            f"Approval hash mismatch for item {item_code}: expected '{expected_hash}', got '{provided_hash}'."
        )

    approval = Approval(
        id=str(uuid.uuid4()),
        item_code=item_code,
        payload=payload,
        payload_hash=expected_hash,
        approved_at=datetime.now(UTC),
        channel=channel,
    )
    session.add(approval)
    return approval


def has_valid_approval(session: Session, item_code: str, payload_hash: str) -> bool:
    stmt = select(Approval).where(
        Approval.item_code == item_code,
        Approval.payload_hash == payload_hash,
    )
    return session.execute(stmt).scalar_one_or_none() is not None
