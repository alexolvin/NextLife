"""Lot deletion service with active deal protection, mandatory unpublishing, and audit.

TZ v2.0 Section I.3:
- Refuses deletion if an active deal exists, providing the exact reason.
- Mandatorily unpublishes/archives all active marketplace listings prior to deletion.
- Soft delete: sets lot status to DELETED (or CANCELLED), retaining the record for audit.
- Preserves media originals (photos are never deleted without explicit separate confirmation).
- Logs audit event: lot remains fully traceable in audit trail.
- Module length <= 400 lines.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ItemStatus
from nextlife.obs.audit import record_audit_event
from nextlife.services.unpublish import unpublish_listings_now
from nextlife.storage.models import Deal, Item, Listing, Lot


class LotDeletionBlockedError(NextLifeError):
    """Raised when lot deletion is rejected due to active deal or constraint."""


def can_delete_lot(session: Session, lot_code: str) -> tuple[bool, str | None]:
    """Check whether a lot can be safely deleted.

    Returns (can_delete, rejection_reason).
    """
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        return False, f"Лот с кодом #{lot_code} не найден."

    # Check for active deals linked to any listing of this item/lot
    listings = session.scalars(
        select(Listing).where(Listing.item_code == lot.item_id)
    ).all()
    listing_ids = [lst.id for lst in listings]

    if listing_ids:
        active_deals = session.scalars(
            select(Deal).where(
                Deal.listing_id.in_(listing_ids),
                Deal.status.in_(["ACTIVE", "OPEN", "IN_PROGRESS", "PAID", "SHIPPED"]),
                Deal.closed_at.is_(None),
            )
        ).all()
        if active_deals:
            deal_ids = ", ".join(d.id for d in active_deals)
            return (
                False,
                f"Невозможно удалить лот #{lot_code}: обнаружена активная сделка (id: {deal_ids}). "
                f"Сделка должна быть завершена или отменена перед удалением лота.",
            )

    return True, None


def delete_lot(
    session: Session,
    lot_code: str,
    *,
    force_unpublish: bool = True,
    actor: str = "OWNER",
) -> dict[str, Any]:
    """Delete lot safely with checks, unpublishing, and audit.

    DoD:
    - Rejection on active deal with detailed explanation.
    - Mandatory unpublishing from marketplaces prior to deletion.
    - Soft delete: record preserved, status set to DELETED.
    - Media assets preserved.
    - Audit event logged.
    """
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ValueError(f"Lot with code {lot_code} not found")

    item = session.scalar(select(Item).where(Item.code == lot.item_id))
    if not item:
        raise ValueError(f"Item {lot.item_id} linked to lot {lot_code} not found")

    # 1. Active deal check
    can_delete, reason = can_delete_lot(session, lot_code)
    if not can_delete:
        raise LotDeletionBlockedError(reason)

    # 2. Mandatory unpublishing from marketplaces (ТЗ «Склад» §2.1.6):
    # через коннекторы реестра, не локальная установка ARCHIVED.
    unpublish_results = unpublish_listings_now(session, item.code, actor=actor)
    unpublished_keys = [r["marketplace_key"] for r in unpublish_results]

    # 3. Soft delete the lot
    previous_lot_status = lot.status
    lot.status = "DELETED"

    # If item has no other non-deleted lots, update item status
    other_active_lots = session.scalars(
        select(Lot).where(Lot.item_id == item.code, Lot.code != lot_code, Lot.status != "DELETED")
    ).all()
    if not other_active_lots and item.status != ItemStatus.SOLD.value:
        item.status = ItemStatus.CANCELLED.value

    # 4. Audit trail: ensure deleted lot is permanently traceable
    record_audit_event(
        session=session,
        actor=actor,
        action="LOT_DELETED",
        entity="lot",
        entity_id=lot.code,
        data={
            "item_code": item.code,
            "previous_status": previous_lot_status,
            "new_status": "DELETED",
            "unpublished_marketplaces": unpublished_keys,
            "media_preserved": True,
        },
    )

    return {
        "success": True,
        "lot_code": lot.code,
        "item_code": item.code,
        "status": "DELETED",
        "unpublished_marketplaces": unpublished_keys,
        "media_preserved": True,
    }
