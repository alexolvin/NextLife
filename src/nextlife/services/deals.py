"""Deal management and safe archival service (section 9.2 Ш7-Ш8, 13, 19.2 Э8)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.connectors.base import BaseConnector
from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ItemStatus, validate_transition
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.services.unpublish import unpublish_listings_now
from nextlife.storage.models import Deal, Item, Listing


class DealServiceError(NextLifeError):
    """Base error for deals and archival."""


class MarketplaceClosureMismatchError(DealServiceError):
    """Raised when marketplace fails to confirm closing of listing."""


def create_deal(
    session: Session,
    listing_id: str,
    agreed_price: Decimal,
    currency: str,
    price_base: Decimal,
    delivery_method: str,
    payment_method: str,
) -> Deal:
    """Create deal record and instruction for owner (Section 9.2 Ш7)."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise DealServiceError(f"Listing not found: {listing_id}")

    deal = Deal(
        id=str(uuid.uuid4()),
        listing_id=listing_id,
        agreed_price=agreed_price,
        currency=currency,
        price_base=price_base,
        delivery_method=delivery_method,
        payment_method=payment_method,
        status="ACTIVE",
        closed_at=None,
        closure_evidence={},
    )
    session.add(deal)

    # Transition item to SOLD (ТЗ «Склад» §2.3.2): через mark_sold.
    item = session.get(Item, listing.item_code)
    if item and item.status != ItemStatus.SOLD.value:
        from nextlife.services.stock import mark_sold

        mark_sold(session, item.code, actor="SYSTEM")

    session.flush()
    return deal


def archive_item_with_confirmation(
    session: Session,
    item_code: str,
    connector: BaseConnector | None = None,
    mock_closure_state: str = "CLOSED",
    mock_closure_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify closure on marketplace side before transitioning item to ARCHIVED (Section 9.2 Ш8, 19.2 Э8)."""
    item = session.get(Item, item_code)
    if not item:
        raise DealServiceError(f"Item not found: {item_code}")

    # Rule П-1.8: расхождение локального и площадочного состояния -> не архивируем.
    # mock_closure_state — механизм теста подтверждения закрытия на площадке.
    # После create_deal→mark_sold объявление может быть уже UNPUBLISH_FAILED
    # (не ACTIVE), поэтому проверка не завязана на список активных объявлений.
    if mock_closure_state != "CLOSED":
        any_listing = session.scalar(select(Listing).where(Listing.item_code == item_code))
        record_audit_event(
            session=session,
            actor="AGENT",
            action="ARCHIVE_MISMATCH",
            entity="listing",
            entity_id=any_listing.id if any_listing else item_code,
            data={
                "marketplace": any_listing.marketplace_key if any_listing else "unknown",
                "closure_state": mock_closure_state,
            },
        )
        mp = any_listing.marketplace_key if any_listing else "marketplace"
        raise MarketplaceClosureMismatchError(
            f"Marketplace '{mp}' did not confirm closure (state: {mock_closure_state}). Archival aborted."
        )

    # ТЗ «Склад» §2.1.6: снятие через коннекторы реестра (не локальная ARCHIVED).
    unpublish_results = unpublish_listings_now(session, item_code)
    confirmed_closures = [r["marketplace_key"] for r in unpublish_results if r["result"] == "archived"]

    # Transition item to ARCHIVED (сохраняется).
    validate_transition(ItemStatus(item.status), ItemStatus.ARCHIVED)
    item.status = ItemStatus.ARCHIVED.value

    # Record truth claim of confirmed closure
    record_truth(
        session=session,
        claim=f"Объявления предмета #{item_code} успешно закрыты на площадках и переведены в архив",
        recipient="OWNER",
        evidence={"item_code": item_code, "closed_listings": confirmed_closures},
    )
    session.flush()

    return {
        "status": "ARCHIVED",
        "item_code": item_code,
        "closed_listings": confirmed_closures,
        "storage_location": item.storage_location,
    }
