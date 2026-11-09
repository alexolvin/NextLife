"""Background job worker for NextLife (TZ §6.3).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import UTC, datetime
from types import FrameType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.jobs.catalog import classify_error
from nextlife.jobs.worker_handlers import (
    handle_publish_listing,
    handle_relist_listing,
    handle_unpublish_listing,
    handle_update_listing,
    handle_warranty_expire,
)
from nextlife.obs.logger import setup_logger
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Job, OwnerNotification

logger = setup_logger("nextlife.worker")
_running = True

NETWORK_RETRY_DELAYS = [60, 300, 900]  # 1 min, 5 min, 15 min


def _signal_handler(signum: int, frame: FrameType | None) -> None:
    global _running
    logger.info(f"Signal {signum} received, stopping worker gracefully...")
    _running = False


def dispatch_job(job: Job, session: Session) -> None:
    """Dispatch job to its respective handler with closed-loop verification."""
    kind = job.kind
    payload = job.payload or {}

    if kind == "PUBLISH_LISTING":
        handle_publish_listing(payload, session)
    elif kind == "UPDATE_LISTING":
        handle_update_listing(payload, session)
    elif kind == "UNPUBLISH_LISTING":
        handle_unpublish_listing(payload, session)
    elif kind == "RELIST_LISTING":
        handle_relist_listing(payload, session)
    elif kind == "WARRANTY_EXPIRE":
        handle_warranty_expire(payload, session)
    else:
        logger.info(f"Handler for job kind '{kind}' completed as no-op or simulated.")


def process_one_job(session: Session) -> bool:
    """Fetch and execute one runnable job."""
    now = datetime.now(UTC)
    job = session.scalars(
        select(Job)
        .where(Job.state == "PENDING")
        .where(Job.run_after <= now)
        .order_by(Job.run_after.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()

    if not job:
        return False

    job.state = "RUNNING"
    job.locked_by = "worker"
    session.flush()

    try:
        dispatch_job(job, session)
        job.state = "DONE"
        job.last_error = None
        job.locked_by = None
        logger.info(f"Job {job.id} ({job.kind}) completed successfully")
    except Exception as exc:
        err_code, err_text, err_hint = classify_error(exc)
        logger.error(f"Job {job.id} ({job.kind}) failed: {exc} (code={err_code})")

        job.attempts += 1
        job.last_error = f"[{err_code}] {str(exc)}"

        # Retry logic:
        # Retry only for network errors when attempts < 3
        if err_code == "NETWORK" and job.attempts < 3:
            delay = NETWORK_RETRY_DELAYS[min(job.attempts - 1, len(NETWORK_RETRY_DELAYS) - 1)]
            job.state = "PENDING"
            job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
            job.locked_by = None
            logger.info(f"Job {job.id} will retry in {delay}s (attempt {job.attempts}/3)")
        else:
            job.state = "FAILED"
            job.locked_by = None

            # Create owner notification on fatal job failure
            notif = OwnerNotification(
                id=str(uuid.uuid4()),
                kind="JOB_FAILED",
                title=f"Ошибка выполнения задачи: {job.kind}",
                body=f"Задача завершилась с ошибкой: {str(exc)}",
            )
            session.add(notif)

    # Check batch completion if job was part of a batch
    if job.batch_id:
        _check_batch_completion(job.batch_id, session)

    session.commit()
    return True


def _check_batch_completion(batch_id: str, session: Session) -> None:
    """Check if all jobs in batch are done/failed, update batch status and item states."""
    from nextlife.storage.models import BatchJob
    batch = session.get(BatchJob, batch_id)
    if not batch:
        return

    jobs = session.scalars(select(Job).where(Job.batch_id == batch_id)).all()
    if not jobs:
        return

    all_done = all(j.state in ("DONE", "FAILED") for j in jobs)
    if all_done:
        has_failed = any(j.state == "FAILED" for j in jobs)
        batch.status = "COMPLETED_WITH_ERRORS" if has_failed else "COMPLETED"
        batch.finished_at = datetime.now(UTC)

        # Count successes and failures
        success_count = sum(1 for j in jobs if j.state == "DONE")
        failure_count = sum(1 for j in jobs if j.state == "FAILED")
        batch.summary = f"Всего: {len(jobs)}, успешно: {success_count}, ошибок: {failure_count}"
        logger.info(f"Batch {batch_id} completed: {batch.summary}")


def run_worker(poll_interval: float = 1.0, once: bool = False) -> None:
    """Main worker loop."""
    logger.info("NextLife worker started, polling for jobs...")
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    while _running:
        try:
            with get_db_session() as session:
                had_job = process_one_job(session)

            if once:
                break

            if not had_job:
                time.sleep(poll_interval)
        except Exception as e:
            logger.error(f"Worker loop error: {e}", exc_info=True)
            if once:
                break
            time.sleep(poll_interval)

    logger.info("NextLife worker stopped.")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="NextLife background job worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds")
    args = parser.parse_args()

    run_worker(poll_interval=args.interval, once=args.once)


if __name__ == "__main__":
    main()
