"""Privacy and Personal Identifiable Information (PII) retention service (T-057, Section II.1 & III).

DoD:
- Retention period for personal data configured in nextlife.yaml (pii_retention_days).
- Clean-up task applies retention rule to purge and anonymize expired counterparty data.
- Records full audit event for privacy purges.
- Module length <= 400 lines.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.storage.models.deals import Deal, Party


class PrivacyError(NextLifeError):
    """Base error for privacy operations."""


TERMINAL_DEAL_STATUSES = {"COMPLETED", "CANCELLED", "ARCHIVED", "RETURNED"}


def purge_expired_pii(
    session: Session,
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Purge and anonymize counterparty personal data older than retention deadline.

    DoD:
    - Retention period loaded from config if not explicitly passed.
    - Anonymizes party records for closed deals older than the deadline.
    - Clears phone, email, address, bank_account and sets anonymized=True.
    - Preserves transaction accounting integrity while zeroing personal data.
    """
    if retention_days is None:
        cfg = load_config()
        retention_days = cfg.app.pii_retention_days

    if retention_days < 0:
        raise PrivacyError("retention_days cannot be negative")

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(days=retention_days)

    # 1. Find deals in terminal state closed before or at cutoff
    deals = session.scalars(
        select(Deal).where(
            Deal.status.in_(TERMINAL_DEAL_STATUSES),
            Deal.closed_at <= cutoff,
            Deal.pii_cleared_at.is_(None),
        )
    ).all()

    purged_deals_count = 0
    anonymized_parties_set = set()

    for deal in deals:
        deal.pii_cleared_at = current_time
        purged_deals_count += 1

        # Check associated party
        if deal.party_id:
            party = session.get(Party, deal.party_id)
            if party and not party.anonymized:
                # Verify party has no active or non-expired deals
                other_active_deals = session.scalars(
                    select(Deal).where(
                        Deal.party_id == party.id,
                        Deal.id != deal.id,
                        Deal.status.not_in(TERMINAL_DEAL_STATUSES),
                    )
                ).all()

                if not other_active_deals:
                    party.name = f"Anonymized Party {party.id[:8]}"
                    party.phone = None
                    party.email = None
                    party.shipping_address = None
                    party.bank_account = None
                    party.anonymized = True
                    party.pii_cleared_at = current_time
                    anonymized_parties_set.add(party.id)

        # Clear PII from handover place if present
        if deal.handover and deal.handover.place:
            deal.handover.place = "[PII_PURGED]"

    session.flush()

    record_audit_event(
        session=session,
        actor=actor,
        action="PII_PURGED",
        entity="privacy",
        entity_id="retention_batch",
        data={
            "retention_days": retention_days,
            "cutoff": cutoff.isoformat(),
            "purged_deals_count": purged_deals_count,
            "anonymized_parties_count": len(anonymized_parties_set),
        },
    )

    session.flush()

    return {
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "purged_deals_count": purged_deals_count,
        "anonymized_parties_count": len(anonymized_parties_set),
    }
