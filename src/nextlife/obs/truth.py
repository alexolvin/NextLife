"""Truth assertion logger with strict evidence validation (Section 13, Ш4)."""

from typing import Any

from sqlalchemy.orm import Session

from nextlife.obs.truth_log import TruthAssertionError, record_truth
from nextlife.storage.models import TruthLog


def log_claim(
    claim: str,
    evidence: dict[str, Any] | None = None,
    recipient: str = "OWNER",
    session: Session | None = None,
) -> TruthLog | None:
    """Log an assertion with mandatory evidence dictionary.

    Raises TruthAssertionError if evidence is missing or empty.
    """
    if evidence is None or not isinstance(evidence, dict) or len(evidence) == 0:
        raise TruthAssertionError(
            f"Cannot record truth claim '{claim}' without valid non-empty evidence dictionary."
        )

    if session is not None:
        return record_truth(session, claim, recipient, evidence)
    return None
