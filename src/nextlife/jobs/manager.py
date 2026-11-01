"""Job management service and batch operations (TZ §6.3, §3.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nextlife.core.status import ListingStatus
from nextlife.jobs.catalog import ERROR_CATALOG
from nextlife.storage.models import Job, Listing


def create_job(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    batch_id: str | None = None,
    idempotency_key: str | None = None,
) -> Job:
    """Create and enqueue a background job."""
    if not idempotency_key:
        idempotency_key = f"{kind}:{batch_id or uuid.uuid4()}:{uuid.uuid4()}"

    job = Job(
        id=str(uuid.uuid4()),
        kind=kind,
        payload=payload,
        state="PENDING",
        attempts=0,
        run_after=datetime.now(UTC),
        batch_id=batch_id,
        idempotency_key=idempotency_key,
    )
    session.add(job)
    session.flush()
    return job


def create_publish_batch(session: Session, listing_ids: list[str]) -> str:
    """Create a batch of PUBLISH_LISTING jobs for approved listings."""
    batch_id = str(uuid.uuid4())
    for lid in listing_ids:
        listing = session.get(Listing, lid)
        if not listing:
            continue
        listing.status = ListingStatus.QUEUED.value
        create_job(
            session=session,
            kind="PUBLISH_LISTING",
            payload={"listing_id": lid, "item_code": listing.item_code, "marketplace_key": listing.marketplace_key},
            batch_id=batch_id,
            idempotency_key=f"publish:{lid}:{batch_id}",
        )
    session.flush()
    return batch_id


def get_batch_summary(session: Session, batch_id: str) -> dict[str, Any]:
    """Retrieve full batch execution report."""
    jobs = session.scalars(select(Job).where(Job.batch_id == batch_id)).all()

    total = len(jobs)
    succeeded = sum(1 for j in jobs if j.state == "SUCCESS")
    failed = sum(1 for j in jobs if j.state == "FAILED")
    running = sum(1 for j in jobs if j.state == "RUNNING")
    pending = sum(1 for j in jobs if j.state == "PENDING")

    items: list[dict[str, Any]] = []
    for j in jobs:
        lid = j.payload.get("listing_id")
        listing = session.get(Listing, lid) if lid else None
        error_code = listing.last_error_code if listing else None
        hint = listing.last_error_hint if listing else None
        cat_info = ERROR_CATALOG.get(error_code or "UNKNOWN", {})

        items.append(
            {
                "job_id": j.id,
                "kind": j.kind,
                "state": j.state,
                "marketplace": j.payload.get("marketplace_key") or (listing.marketplace_key if listing else ""),
                "item_code": j.payload.get("item_code") or (listing.item_code if listing else ""),
                "listing_id": lid,
                "listing_title": listing.title if listing else "",
                "external_url": listing.external_url if listing else None,
                "error_code": error_code,
                "error_text": listing.last_error_text if listing else j.last_error,
                "error_hint": hint or cat_info.get("hint"),
                "action_label": cat_info.get("action_label"),
                "action_url": cat_info.get("action_url") or (f"/ui/listings/{lid}" if lid else None),
            }
        )

    is_completed = (running == 0 and pending == 0 and total > 0)
    return {
        "batch_id": batch_id,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "running": running,
        "pending": pending,
        "is_completed": is_completed,
        "items": items,
    }


def get_active_jobs_count(session: Session) -> int:
    """Count of currently active (running or pending) jobs."""
    count = session.scalar(
        select(func.count(Job.id)).where(Job.state.in_(["PENDING", "RUNNING"]))
    )
    return int(count or 0)


def get_active_jobs(session: Session, limit: int = 10) -> list[dict[str, Any]]:
    """List of recent/active jobs for header drawer."""
    jobs = session.scalars(
        select(Job)
        .where(Job.state.in_(["PENDING", "RUNNING"]))
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "state": j.state,
            "batch_id": j.batch_id,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]
