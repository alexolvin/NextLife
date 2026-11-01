"""Saved queries and polling service for buyer searches (TZ v2.0 Section I.3 / Task T-070).

DoD:
- Continuous search based on query criteria with poll interval from config.
- Deduplication: re-discovery of identical listing produces zero repeat notifications.
- Verified by outgoing filter.
- Module length <= 400 lines.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import BuyCandidate, BuyRequest

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
NEXTLIFE_CONFIG_PATH = BASE_DIR / "config" / "nextlife.yaml"
TEMPLATE_PATH = BASE_DIR / "templates" / "comms" / "buy_candidate_found.jinja2"


class SavedQueryError(NextLifeError):
    """Raised on saved query operation error."""


def get_default_poll_interval() -> int:
    """Read poll_interval_seconds from nextlife.yaml config."""
    if NEXTLIFE_CONFIG_PATH.exists():
        with open(NEXTLIFE_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            val = data.get("poll_interval_seconds")
            if val is not None:
                return int(val)
    return 60


def create_saved_buy_request(
    session: Session,
    query: str,
    budget: Decimal | float,
    currency: str = "CZK",
    attrs: dict[str, Any] | None = None,
    poll_interval_seconds: int | None = None,
) -> BuyRequest:
    """Create a persistent saved buy request."""
    interval = poll_interval_seconds or get_default_poll_interval()
    req = BuyRequest(
        id=str(uuid.uuid4()),
        query=query.strip(),
        attrs=attrs or {},
        budget=Decimal(str(budget)),
        currency=currency,
        status="ACTIVE",
        poll_interval_seconds=interval,
        last_polled_at=None,
    )
    session.add(req)
    session.flush()
    return req


def poll_saved_queries(
    session: Session,
    search_executor: Callable[[str, dict[str, Any]], list[dict[str, Any]]],
    marketplace_key: str = "bazos_cz",
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Execute polling for eligible active buy requests with deduplication.

    DoD:
    - Finds active requests due for polling.
    - Matches are deduplicated against existing BuyCandidate entries.
    - No duplicate notifications sent for the same external URL / external ID.
    """
    now = datetime.now(UTC)
    active_requests = session.query(BuyRequest).filter(BuyRequest.status == "ACTIVE").all()

    due_requests = []
    for r in active_requests:
        if r.last_polled_at is None:
            due_requests.append(r)
        else:
            diff = (now - r.last_polled_at).total_seconds()
            if diff >= r.poll_interval_seconds:
                due_requests.append(r)

    total_discovered = 0
    total_new_candidates = 0
    total_notifications = 0

    if not TEMPLATE_PATH.exists():
        raise SavedQueryError(f"Template missing: {TEMPLATE_PATH}")

    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")

    for req in due_requests:
        found_listings = search_executor(req.query, req.attrs)
        total_discovered += len(found_listings)

        for item in found_listings:
            ext_url = item.get("external_url", "").strip()
            ext_id = item.get("external_id")
            price = Decimal(str(item.get("price", "0.00")))
            curr = item.get("currency", req.currency)
            title = item.get("title", req.query)

            # Check for existing candidate for this request_id
            existing = (
                session.query(BuyCandidate)
                .filter(
                    BuyCandidate.request_id == req.id,
                    BuyCandidate.external_url == ext_url,
                )
                .first()
            )

            if existing:
                # Deduplication rule: already known listing, NEVER notify again!
                continue

            # Create new candidate
            candidate = BuyCandidate(
                id=str(uuid.uuid4()),
                request_id=req.id,
                marketplace_key=marketplace_key,
                external_url=ext_url,
                external_id=ext_id,
                price=price,
                currency=curr,
                price_base=price,
                verdict="NEW_CANDIDATE",
                contacted=False,
                notified_owner=True,
            )
            session.add(candidate)
            session.flush()
            total_new_candidates += 1

            # Format notification
            context = {
                "query": req.query,
                "marketplace_key": marketplace_key,
                "title": title,
                "price": str(price),
                "currency": curr,
                "budget": str(req.budget),
                "external_url": ext_url,
            }
            check_context_for_internal_fields(context)
            notify_body = render_outgoing_template(template_str, context)

            enqueue_outbox(
                session=session,
                transport="TELEGRAM",
                recipient_hint="OWNER",
                body=notify_body,
                idempotency_key=f"buy-found-{candidate.id}",
            )
            total_notifications += 1

            record_audit_event(
                session=session,
                actor=actor,
                action="BUY_CANDIDATE_DISCOVERED",
                entity="buy_candidate",
                entity_id=candidate.id,
                data={
                    "request_id": req.id,
                    "query": req.query,
                    "price": float(price),
                    "external_url": ext_url,
                },
            )
            record_truth(
                session=session,
                claim="BUY_CANDIDATE_FOUND",
                recipient="OWNER",
                evidence={
                    "candidate_id": candidate.id,
                    "request_id": req.id,
                    "external_url": ext_url,
                },
            )

        req.last_polled_at = now

    session.flush()

    return {
        "due_requests_count": len(due_requests),
        "total_discovered": total_discovered,
        "new_candidates": total_new_candidates,
        "notifications_sent": total_notifications,
    }
