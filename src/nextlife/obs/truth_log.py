"""Truth assertion log recorder with strict evidence enforcement."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models import TruthLog


class TruthAssertionError(NextLifeError):
    """Raised when an assertion is attempted without evidence."""


def record_truth(
    session: Session,
    claim: str,
    recipient: str,
    evidence: dict[str, Any],
) -> TruthLog:
    if not evidence or not isinstance(evidence, dict):
        raise TruthAssertionError(
            f"Cannot record truth claim '{claim}' without valid evidence dictionary."
        )

    entry = TruthLog(
        id=str(uuid.uuid4()),
        at=datetime.now(UTC),
        claim=claim,
        recipient=recipient,
        evidence=evidence,
    )
    session.add(entry)
    return entry
