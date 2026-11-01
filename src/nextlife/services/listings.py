"""Service for listing management, preparation, approval, and relisting (TZ §5.6, §6).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ListingStatus
from nextlife.jobs.manager import create_job, create_publish_batch
from nextlife.obs.audit import record_audit_event
from nextlife.storage.models import Item, ItemPhoto, Listing, OwnerNotification


class ListingValidationError(NextLifeError):
    """Raised when listing validation fails (e.g. approval criteria)."""


def _get_marketplace_cfg(key: str) -> Any | None:
    cfg = load_config()
    return cfg.marketplaces.get(key)


def get_marketplace_capabilities(key: str) -> dict[str, bool]:
    mp = _get_marketplace_cfg(key)
    if not mp:
        return {}
    caps = getattr(mp, "capabilities", {})
    if not isinstance(caps, dict):
        caps = dict(caps) if hasattr(caps, "__iter__") else {}
    return {
        "search": bool(caps.get("search", False)),
        "publish": bool(caps.get("publish", False) or caps.get("post", False)),
        "update": bool(caps.get("update", False) or caps.get("edit", False)),
        "unpublish": bool(caps.get("unpublish", False)),
        "relist": bool(caps.get("relist", False) or caps.get("bump", False)),
        "messages_read": bool(caps.get("messages_read", False)),
        "messages_send": bool(caps.get("messages_send", False)),
        "delivery_parse": bool(caps.get("delivery_parse", False)),
    }


def prepare_item_listings(session: Session, item_code: str) -> list[Listing]:
    """Prepare DRAFT listings for recommended marketplaces (§5.6)."""
    item = session.get(Item, item_code)
    if not item:
        raise ListingValidationError(f"Предмет #{item_code} не найден")

    config = load_config()
    # Recommended marketplaces: publish=True, sorted by analogues or default
    recommended_keys = []
    for k, _mp in config.marketplaces.items():
        caps = get_marketplace_capabilities(k)
        if caps.get("publish"):
            recommended_keys.append(k)

    # Preferred order: bazos_cz, sbazar_cz, aukro_cz first if present
    priority_order = ["bazos_cz", "sbazar_cz", "aukro_cz"]
    recommended_keys.sort(
        key=lambda x: priority_order.index(x) if x in priority_order else 99
    )

    created_or_existing = []
    price_val = item.target_price or item.price_estimate_median or Decimal("0")

    # Collect item photo ids (non-defect first)
    photos = (
        session.execute(
            select(ItemPhoto)
            .where(ItemPhoto.item_code == item_code)
            .order_by(ItemPhoto.created_at.asc())
        )
        .scalars()
        .all()
    )
    photo_ids = [p.id for p in photos]

    for mp_key in recommended_keys:
        # Check if active or draft listing already exists
        existing = session.scalar(
            select(Listing).where(
                Listing.item_code == item_code,
                Listing.marketplace_key == mp_key,
                Listing.status.notin_([ListingStatus.ARCHIVED.value, "DELETED"]),
            )
        )
        if existing:
            created_or_existing.append(existing)
            continue

        mp_cfg = config.marketplaces[mp_key]
        currency = getattr(mp_cfg, "currency", "CZK")
        lang = getattr(mp_cfg, "lang", item.description_lang or "cs")

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key=mp_key,
            status=ListingStatus.DRAFT.value,
            title=item.title[:60] if item.title else item_code,
            description=item.description or "",
            category_key="",
            category_path="",
            category_auto=True,
            price=price_val,
            currency=currency,
            price_base=price_val,
            lang=lang,
            photo_ids=photo_ids,
            is_free=False,
        )
        session.add(listing)
        created_or_existing.append(listing)

    record_audit_event(
        session=session,
        actor="USER",
        action="LISTINGS_PREPARED",
        entity="ITEM",
        entity_id=item_code,
        data={"count": len(created_or_existing)},
    )
    session.flush()
    return created_or_existing


def approve_listing(session: Session, listing_id: str) -> Listing:
    """Approve listing per §2.3 requirements."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    # Criteria: title, description, category, price, >= 1 photo
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

    if "title" in data:
        listing.title = data["title"].strip()
    if "description" in data:
        listing.description = data["description"].strip()
    if "category_key" in data or "category_path" in data:
        listing.category_key = data.get("category_key", listing.category_key)
        listing.category_path = data.get("category_path", listing.category_path)
        listing.category_auto = False
    if "price" in data and data["price"] is not None:
        listing.price = Decimal(str(data["price"]))
        listing.price_base = listing.price
    if "photo_ids" in data:
        listing.photo_ids = data["photo_ids"]

    # If published, create background update job
    if listing.status in (ListingStatus.PUBLISHED.value, "ACTIVE"):
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

    if listing.status in (ListingStatus.PUBLISHED.value, "ACTIVE"):
        listing.status = ListingStatus.UPDATING.value
        create_job(
            session=session,
            kind="UPDATE_LISTING",
            payload={"is_free": is_free, "price": "0", "listing_id": listing.id},
        )

    session.flush()
    return listing


def unpublish_listing(session: Session, listing_id: str) -> dict[str, Any]:
    """Unpublish listing from marketplace (§6.2)."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    caps = get_marketplace_capabilities(listing.marketplace_key)
    if not caps.get("unpublish"):
        return {
            "supported": False,
            "message": "Снятие не поддерживается площадкой: снимите вручную",
            "external_url": listing.external_url,
        }

    job = create_job(
        session=session,
        kind="UNPUBLISH_LISTING",
        payload={"listing_id": listing.id, "external_id": listing.external_id},
    )
    listing.status = ListingStatus.ARCHIVED.value
    listing.archived_at = datetime.now(UTC)
    session.flush()

    return {"supported": True, "job_id": job.id, "status": "QUEUED"}


def relist_listing_now(session: Session, listing_id: str) -> dict[str, Any]:
    """Manually relist listing (§6.2)."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    caps = get_marketplace_capabilities(listing.marketplace_key)
    if not caps.get("relist"):
        return {
            "supported": False,
            "message": "Перевыставление не поддерживается площадкой",
            "external_url": listing.external_url,
        }

    job = create_job(
        session=session,
        kind="RELIST_LISTING",
        payload={"listing_id": listing.id, "previous_listing_id": listing.id},
    )
    session.flush()

    return {"supported": True, "job_id": job.id, "status": "QUEUED"}


def delete_listing(session: Session, listing_id: str) -> None:
    """Delete listing record from system."""
    listing = session.get(Listing, listing_id)
    if listing:
        session.delete(listing)
        session.flush()


def publish_approved_listings(session: Session, item_code: str | None = None) -> str:
    """Batch publish all approved listings (§6.3)."""
    stmt = select(Listing).where(Listing.status == ListingStatus.APPROVED.value)
    if item_code:
        stmt = stmt.where(Listing.item_code == item_code)

    approved = session.scalars(stmt).all()
    if not approved:
        raise ListingValidationError("Нет утверждённых объявлений для размещения")

    batch_id = create_publish_batch(session=session, listing_ids=[item.id for item in approved])

    record_audit_event(
        session=session,
        actor="USER",
        action="BATCH_PUBLISH_STARTED",
        entity="BATCH",
        entity_id=batch_id,
        data={"count": len(approved), "item_code": item_code},
    )
    session.flush()
    return batch_id


def get_listings_counts(session: Session) -> dict[str, int]:
    """Calculate counts for listing chips (§6.1)."""
    now = datetime.now(UTC)
    three_days = now + timedelta(days=3)

    def _c(condition):
        return session.scalar(select(func.count(Listing.id)).where(condition)) or 0

    return {
        "published": _c(Listing.status.in_([ListingStatus.PUBLISHED.value, "ACTIVE"])),
        "errors": _c(Listing.status.in_([ListingStatus.FAILED.value, ListingStatus.UNPUBLISH_FAILED.value])),
        "approved": _c(Listing.status == ListingStatus.APPROVED.value),
        "drafts": _c(Listing.status == ListingStatus.DRAFT.value),
        "expiring": _c(
            (Listing.status.in_([ListingStatus.PUBLISHED.value, "ACTIVE"]))
            & (Listing.expires_at.is_not(None))
            & (Listing.expires_at <= three_days)
        ),
        "archived": _c(Listing.status.in_([ListingStatus.ARCHIVED.value, "ARCHIVED"])),
    }


def check_and_enqueue_expired_relistings(session: Session) -> dict[str, Any]:
    """Hourly relisting task (§6.4)."""
    now = datetime.now(UTC)
    expired_listings = session.scalars(
        select(Listing).where(
            Listing.status.in_([ListingStatus.PUBLISHED.value, "ACTIVE"]),
            Listing.expires_at.is_not(None),
            Listing.expires_at <= now,
        )
    ).all()

    relisted_count = 0
    expired_count = 0

    for listing in expired_listings:
        caps = get_marketplace_capabilities(listing.marketplace_key)
        if caps.get("relist"):
            create_job(
                session=session,
                kind="RELIST_LISTING",
                payload={"listing_id": listing.id, "previous_listing_id": listing.id},
            )
            relisted_count += 1
        else:
            listing.status = ListingStatus.EXPIRED.value
            notif = OwnerNotification(
                id=str(uuid.uuid4()),
                type="LISTING_EXPIRED",
                severity="WARNING",
                title=f"Истёк срок показа на {listing.marketplace_key}",
                body=f"Объявление предмета #{listing.item_code} завершило показ. Площадка не поддерживает авто-перевыставление.",
                link=f"/ui/listings/{listing.id}",
                entity_type="listing",
                entity_id=listing.id,
            )
            session.add(notif)
            expired_count += 1

    session.flush()
    return {"relisted": relisted_count, "expired": expired_count}
