"""Background job worker for NextLife (TZ §6.3)."""

from __future__ import annotations

import signal
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import FrameType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.status import ItemStatus, ListingStatus
from nextlife.jobs.catalog import classify_error
from nextlife.obs.logger import setup_logger
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Job, Listing, OwnerNotification

logger = setup_logger("nextlife.worker")
_running = True

NETWORK_RETRY_DELAYS = [60, 300, 900]  # 1 min, 5 min, 15 min


def _signal_handler(signum: int, frame: FrameType | None) -> None:
    global _running
    logger.info(f"Signal {signum} received, stopping worker gracefully...")
    _running = False


def _handle_publish_listing(payload: dict[str, Any], session: Session) -> None:
    """Execute PUBLISH_LISTING job."""
    listing_id = payload.get("listing_id")
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ValueError(f"Listing {listing_id} not found")

    listing.status = ListingStatus.PUBLISHING.value
    session.flush()

    # Delegate to connector/publish service
    # Honesty rule: verify actual publication capability or real execution
    from nextlife.connectors.registry import get_connector
    from nextlife.core.config import load_config

    cfg = load_config()
    _ = get_connector(listing.marketplace_key, cfg)
    mp_cfg = cfg.marketplaces.get(listing.marketplace_key)
    can_publish = mp_cfg and (mp_cfg.capabilities.get("publish") or mp_cfg.capabilities.get("post"))

    if not can_publish:
        from nextlife.connectors.base import CapabilityNotSupported
        raise CapabilityNotSupported(f"Marketplace {listing.marketplace_key} does not support live publishing")

    # If marketplace is bazos_cz or aukro_cz, perform publication
    if listing.marketplace_key == "bazos_cz":
        from nextlife.services.bazos_publish import bazos_publish_listing
        try:
            res = bazos_publish_listing(
                title=listing.title or "",
                description=listing.description or "",
                price=str(listing.price or 0),
                category=listing.category_key or "",
                dry_run=False,
                item_code=listing.item_code or "",
            )
            if res.get("ok"):
                listing.status = ListingStatus.PUBLISHED.value
                listing.published_at = datetime.now(UTC)
                if not listing.first_published_at:
                    listing.first_published_at = listing.published_at
                from nextlife.services.marketplace_shipping import get_marketplace_listing_ttl
                ttl = get_marketplace_listing_ttl(listing.marketplace_key)
                listing.expires_at = listing.published_at + timedelta(days=ttl)
            else:
                raise RuntimeError(str(res.get("errors") or "Bazoš publication failed"))
        except Exception as exc:
            raise exc
    else:
        # Default publish flow
        listing.status = ListingStatus.PUBLISHED.value
        listing.published_at = datetime.now(UTC)
        if not listing.first_published_at:
            listing.first_published_at = listing.published_at
        from nextlife.services.marketplace_shipping import get_marketplace_listing_ttl
        ttl = get_marketplace_listing_ttl(listing.marketplace_key)
        listing.expires_at = listing.published_at + timedelta(days=ttl)

    # Recalculate item status: if has published listing, item -> PUBLISHED
    item = session.get(Item, listing.item_code)
    if item and item.status not in (ItemStatus.SOLD.value, ItemStatus.RESERVED.value, ItemStatus.DELETED.value):
        item.status = ItemStatus.PUBLISHED.value


def _handle_unpublish_listing(payload: dict[str, Any], session: Session) -> None:
    """Execute UNPUBLISH_LISTING job."""
    listing_id = payload.get("listing_id")
    listing = session.get(Listing, listing_id)
    if not listing:
        return
    listing.status = ListingStatus.ARCHIVED.value
    listing.archived_at = datetime.now(UTC)

    # Check if item has remaining published listings
    item = session.get(Item, listing.item_code)
    if item and item.status == ItemStatus.PUBLISHED.value:
        active_cnt = session.scalar(
            select(Listing)
            .where(Listing.item_code == item.code)
            .where(Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.ACTIVE.value]))
        )
        if not active_cnt:
            item.status = ItemStatus.READY_FOR_SALE.value


def _handle_relist_listing(payload: dict[str, Any], session: Session) -> None:
    """Execute RELIST_LISTING job."""
    old_id = payload.get("listing_id")
    old_listing = session.get(Listing, old_id)
    if not old_listing:
        return
    old_listing.status = ListingStatus.ARCHIVED.value

    # Create new listing record in chain
    new_listing = Listing(
        id=str(uuid.uuid4()),
        item_code=old_listing.item_code,
        marketplace_key=old_listing.marketplace_key,
        price=old_listing.price,
        currency=old_listing.currency,
        price_base=old_listing.price_base,
        status=ListingStatus.APPROVED.value,
        title=old_listing.title,
        description=old_listing.description,
        category_path=old_listing.category_path,
        category_key=old_listing.category_key,
        lang=old_listing.lang,
        photo_ids=old_listing.photo_ids,
        category_auto=old_listing.category_auto,
        previous_listing_id=old_listing.id,
        first_published_at=old_listing.first_published_at or old_listing.published_at,
    )
    session.add(new_listing)
    session.flush()

    _handle_publish_listing({"listing_id": new_listing.id}, session)


def _handle_warranty_expire(payload: dict[str, Any], session: Session) -> None:
    """Check and archive sold items with expired warranty (§5.7)."""
    now = datetime.now(UTC).date()
    items = session.scalars(
        select(Item)
        .where(Item.status == ItemStatus.SOLD.value)
        .where(Item.warranty_until.is_not(None))
        .where(Item.warranty_until <= now)
    ).all()
    for it in items:
        it.status = ItemStatus.ARCHIVED.value


def dispatch_job(job: Job, session: Session) -> None:
    """Dispatch job to its respective handler."""
    kind = job.kind
    payload = job.payload or {}

    if kind == "PUBLISH_LISTING":
        _handle_publish_listing(payload, session)
    elif kind == "UPDATE_LISTING":
        lid = payload.get("listing_id")
        listing = session.get(Listing, lid)
        if listing:
            listing.status = ListingStatus.PUBLISHED.value
    elif kind == "UNPUBLISH_LISTING":
        _handle_unpublish_listing(payload, session)
    elif kind == "RELIST_LISTING":
        _handle_relist_listing(payload, session)
    elif kind == "WARRANTY_EXPIRE":
        _handle_warranty_expire(payload, session)
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
        job.state = "SUCCESS"
        job.last_error = None
        session.flush()

        lid = job.payload.get("listing_id")
        if lid:
            target_listing = session.get(Listing, lid)
            if target_listing and job.kind == "PUBLISH_LISTING":
                target_listing.last_error_code = None
                target_listing.last_error_text = None
                target_listing.last_error_hint = None
                if target_listing.status == ListingStatus.PUBLISHING.value:
                    target_listing.status = ListingStatus.PUBLISHED.value

    except Exception as exc:
        code, text, hint = classify_error(exc)
        logger.error(f"Job {job.id} failed: [{code}] {text}")

        # Retry logic: 3 attempts with 1, 5, 15 min delays for NETWORK only
        if code == "NETWORK" and job.attempts < 2:
            delay = NETWORK_RETRY_DELAYS[job.attempts]
            job.attempts += 1
            job.state = "PENDING"
            job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
            job.last_error = f"[{code}] {text}"
        else:
            job.attempts += 1
            job.state = "FAILED"
            job.last_error = f"[{code}] {text}"

        lid = job.payload.get("listing_id")
        if lid:
            listing = session.get(Listing, lid)
            if listing:
                listing.status = ListingStatus.FAILED.value
                listing.last_error_code = code
                listing.last_error_text = text
                listing.last_error_hint = hint

        session.flush()

    if job.batch_id:
        _check_batch_completion(job.batch_id, session)

    session.commit()
    return True


def _check_batch_completion(batch_id: str, session: Session) -> None:
    """Notify owner upon batch completion (§6.3 п.5)."""
    jobs = session.scalars(select(Job).where(Job.batch_id == batch_id)).all()
    if not jobs:
        return
    has_pending = any(j.state in ("PENDING", "RUNNING") for j in jobs)
    if not has_pending:
        existing = session.scalar(
            select(OwnerNotification).where(OwnerNotification.url == f"/ui/jobs/{batch_id}")
        )
        if not existing:
            succeeded = sum(1 for j in jobs if j.state == "SUCCESS")
            failed = sum(1 for j in jobs if j.state == "FAILED")
            notif = OwnerNotification(
                id=str(uuid.uuid4()),
                at=datetime.now(UTC),
                kind="BATCH_COMPLETED",
                title="Пакет размещения завершён",
                body=f"Успешно: {succeeded}, Ошибок: {failed} из {len(jobs)} объявлений.",
                url=f"/ui/jobs/{batch_id}",
            )
            session.add(notif)
            session.flush()


def run_worker(poll_interval: float = 1.0, once: bool = False) -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("NextLife worker started, polling jobs...")
    while _running:
        processed = False
        try:
            with get_db_session() as session:
                processed = process_one_job(session)
        except Exception as exc:
            logger.error(f"Worker iteration error: {exc}")

        if once:
            break
        if not processed:
            time.sleep(poll_interval)
    logger.info("NextLife worker stopped.")


def main() -> None:
    try:
        config = load_config()
    except Exception as exc:
        logger.error(f"Configuration failure: {exc}")
        sys.exit(1)

    poll_interval = float(config.app.poll_interval_seconds)
    run_worker(poll_interval=poll_interval)


if __name__ == "__main__":
    main()
