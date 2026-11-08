"""Listing update, approve, counts, and expiration operations.

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ListingStatus
from nextlife.jobs.manager import create_job
from nextlife.obs.audit import record_audit_event
from nextlife.storage.models import Item, Listing, OwnerNotification


class ListingValidationError(NextLifeError):
    """Validation failed for listing action."""


def approve_listing(session: Session, listing_id: str) -> Listing:
    """Approve listing per §2.3 requirements."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    errors = []
    if not (listing.title and listing.title.strip()):
        errors.append("заголовок")
    if not (listing.description and listing.description.strip()):
        errors.append("описание")
    if not (listing.category_key or listing.category_path):
        errors.append("категория")
    if listing.price is None:
        errors.append("цена")
    if not listing.photo_ids:
        errors.append("хотя бы 1 фото")

    if errors:
        raise ListingValidationError(f"Нельзя утвердить объявление. Не заполнено: {', '.join(errors)}")

    listing.status = ListingStatus.APPROVED.value
    record_audit_event(
        session=session,
        actor="USER",
        action="LISTING_APPROVED",
        entity="LISTING",
        entity_id=listing.id,
        data={"marketplace": listing.marketplace_key},
    )
    session.flush()
    return listing


def unapprove_listing(session: Session, listing_id: str) -> Listing:
    """Revert approved listing back to DRAFT."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    listing.status = ListingStatus.DRAFT.value
    record_audit_event(
        session=session,
        actor="USER",
        action="LISTING_UNAPPROVED",
        entity="LISTING",
        entity_id=listing.id,
        data={"marketplace": listing.marketplace_key},
    )
    session.flush()
    return listing


def update_listing(session: Session, listing_id: str, data: dict[str, Any]) -> Listing:
    """Update listing attributes and trigger UPDATE_LISTING if published."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    marketplace_fields_changed = False
    if "title" in data:
        new_title = data["title"].strip()
        if new_title != (listing.title or "").strip():
            listing.title = new_title
            marketplace_fields_changed = True
    if "description" in data:
        new_desc = data["description"].strip()
        if new_desc != (listing.description or "").strip():
            listing.description = new_desc
            marketplace_fields_changed = True
    if "category_key" in data or "category_path" in data:
        new_cat = data.get("category_key", listing.category_key)
        new_path = data.get("category_path", listing.category_path)
        if new_cat != listing.category_key or new_path != listing.category_path:
            listing.category_key = new_cat
            listing.category_path = new_path
            listing.category_auto = False
            marketplace_fields_changed = True
    if "price" in data and data["price"] is not None:
        new_price = Decimal(str(data["price"]))
        if new_price != listing.price:
            listing.price = new_price
            listing.price_base = listing.price
            marketplace_fields_changed = True
    if "photo_ids" in data:
        new_photos = list(data["photo_ids"])
        if new_photos != list(listing.photo_ids or []):
            listing.photo_ids = new_photos
            marketplace_fields_changed = True
    if "auto_relist" in data:
        listing.auto_relist = bool(data["auto_relist"])

    if listing.status in (ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value) and marketplace_fields_changed:
        listing.status = ListingStatus.UPDATING.value
        create_job(
            session=session,
            kind="UPDATE_LISTING",
            payload={"price": str(listing.price), "title": listing.title, "listing_id": listing.id},
        )

    record_audit_event(
        session=session,
        actor="USER",
        action="LISTING_UPDATED",
        entity="LISTING",
        entity_id=listing.id,
        data=data,
    )
    session.flush()
    return listing


def set_listing_free(session: Session, listing_id: str, is_free: bool = True) -> Listing:
    """Mark listing as free giveaway (§6.2)."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    listing.is_free = is_free
    if is_free:
        listing.price = Decimal("0")
        notice = "\n\n(Daruji za odvoz / Отдам бесплатно)"
        if notice not in (listing.description or ""):
            listing.description = (listing.description or "") + notice

    if listing.status in (ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value):
        listing.status = ListingStatus.UPDATING.value
        create_job(
            session=session,
            kind="UPDATE_LISTING",
            payload={"is_free": is_free, "price": "0", "listing_id": listing.id},
        )

    session.flush()
    return listing


def get_listings_counts(session: Session, item_code: str | None = None) -> dict[str, int]:
    """Calculate counts for listing chips (§6.1). Excludes listings of deleted items."""
    now = datetime.now(UTC)
    three_days = now + timedelta(days=3)

    def _c(condition):
        stmt = (
            select(func.count(Listing.id))
            .join(Item, Item.code == Listing.item_code)
            .where(Item.status != "DELETED")
            .where(condition)
        )
        if item_code:
            stmt = stmt.where(Listing.item_code == item_code)
        return session.scalar(stmt) or 0

    return {
        "all": _c(Listing.status.is_not(None)),
        "published": _c(Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value])),
        "errors": _c(Listing.status.in_([ListingStatus.FAILED.value, ListingStatus.UNPUBLISH_FAILED.value])),
        "approved": _c(Listing.status == ListingStatus.APPROVED.value),
        "drafts": _c(Listing.status == ListingStatus.DRAFT.value),
        "expiring": _c(
            (Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value]))
            & (Listing.expires_at.is_not(None))
            & (Listing.expires_at <= three_days)
        ),
        "archived": _c(Listing.status.in_([ListingStatus.ARCHIVED.value, "ARCHIVED"])),
    }


def check_and_enqueue_expired_relistings(session: Session) -> dict[str, Any]:
    """Hourly relisting task (§6.4)."""
    from nextlife.services.listings import get_marketplace_capabilities

    now = datetime.now(UTC)
    expired_listings = session.scalars(
        select(Listing).where(
            Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value]),
            Listing.expires_at.is_not(None),
            Listing.expires_at <= now,
        )
    ).all()

    relisted_count = 0
    expired_count = 0

    for listing in expired_listings:
        caps = get_marketplace_capabilities(listing.marketplace_key)
        if caps.get("relist") and getattr(listing, "auto_relist", False):
            create_job(
                session=session,
                kind="RELIST_LISTING",
                payload={"listing_id": listing.id, "previous_listing_id": listing.id},
            )
            relisted_count += 1
        else:
            listing.status = ListingStatus.EXPIRED.value
            body = (
                f"Объявление предмета #{listing.item_code} завершило показ. Авто-перевыставление отключено."
                if caps.get("relist")
                else f"Объявление предмета #{listing.item_code} завершило показ. Площадка не поддерживает авто-перевыставление."
            )
            notif = OwnerNotification(
                id=str(uuid.uuid4()),
                kind="LISTING_EXPIRED",
                title=f"Истёк срок показа на {listing.marketplace_key}",
                body=body,
                url=f"/ui/listings/{listing.id}",
            )
            session.add(notif)
            expired_count += 1

    session.flush()
    return {"relisted": relisted_count, "expired": expired_count}
