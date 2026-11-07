"""Service for listing management, preparation, approval, and relisting (TZ §5.6, §6).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ListingStatus
from nextlife.jobs.manager import create_job, create_publish_batch
from nextlife.obs.audit import record_audit_event
from nextlife.storage.models import Item, ItemPhoto, Listing, Marketplace, OwnerNotification


class ListingValidationError(NextLifeError):
    """Raised when listing validation fails (e.g. approval criteria)."""


def _get_marketplace_cfg(key: str) -> Any | None:
    cfg = load_config()
    return cfg.marketplaces.get(key)


def get_marketplace_capabilities(key: str) -> dict[str, bool]:
    mp = _get_marketplace_cfg(key)
    caps = getattr(mp, "capabilities", {}) if mp else {}
    if not isinstance(caps, dict):
        caps = dict(caps) if hasattr(caps, "__iter__") else {}
    return {
        "search": bool(caps.get("search", False)),
        "publish": bool(caps.get("publish", False) or caps.get("post", False)),
        "update": bool(caps.get("update", False) or caps.get("edit", False)),
        "unpublish": bool(caps.get("unpublish", False) or caps.get("archive", False)),
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
    recommended_keys = list(config.marketplaces.keys())

    from nextlife.services.listing_categories import (
        get_marketplace_category_choice,
        infer_item_category,
    )

    if not item.category_key or item.category_key == "general":
        inferred = infer_item_category(item.title, item.description or "", item.category_key)
        if inferred != "general":
            item.category_key = inferred

    from nextlife.services.category_gate import eligible_publish_keys

    recommended_keys = eligible_publish_keys(config, item.category_key, recommended_keys)

    created_or_existing = []
    price_val = item.target_price or item.price_estimate_median or Decimal("0")

    # Collect item photo ids (non-defect first)
    photos = session.scalars(
        select(ItemPhoto).where(ItemPhoto.item_code == item_code).order_by(ItemPhoto.created_at.asc())
    ).all()
    photo_ids = [p.id for p in photos]

    for mp_key in recommended_keys:
        cat_key, cat_path = get_marketplace_category_choice(
            item.category_key, mp_key, text=f"{item.title or ''} {item.description or ''}", config=config
        )
        existing = session.scalar(
            select(Listing).where(
                Listing.item_code == item_code,
                Listing.marketplace_key == mp_key,
                Listing.status.notin_([ListingStatus.ARCHIVED.value, "DELETED"]),
            )
        )
        if existing:
            if not existing.title:
                existing.title = item.title[:60] if item.title else item_code
            if not existing.description:
                existing.description = item.description or ""
            if not existing.price:
                existing.price = price_val
                existing.price_base = price_val
            if not existing.photo_ids:
                existing.photo_ids = photo_ids
            if not (existing.category_key or existing.category_path):
                existing.category_key, existing.category_path = cat_key, cat_path
            created_or_existing.append(existing)
            continue

        mp_cfg = config.marketplaces[mp_key]
        currency = getattr(mp_cfg, "currency", "CZK")
        lang = getattr(mp_cfg, "lang", item.description_lang or "cs")

        item_currency = (item.target_currency or "CZK").strip().upper()
        target_currency = currency.strip().upper()

        item_price = price_val
        if target_currency != item_currency and item_price:
            from datetime import date

            from nextlife.money.money import get_latest_rate_prior_or_on
            today = date.today()
            try:
                if item_currency == "CZK" and target_currency == "EUR":
                    rate, _ = get_latest_rate_prior_or_on(session, "CZK", today)
                    item_price = (item_price / rate).quantize(Decimal("1.00"))
                elif item_currency == "EUR" and target_currency == "CZK":
                    rate, _ = get_latest_rate_prior_or_on(session, "CZK", today)
                    item_price = (item_price * rate).quantize(Decimal("1.00"))
                elif item_currency == "PLN" and target_currency == "EUR":
                    rate, _ = get_latest_rate_prior_or_on(session, "PLN", today)
                    item_price = (item_price / rate).quantize(Decimal("1.00"))
                elif item_currency == "EUR" and target_currency == "PLN":
                    rate, _ = get_latest_rate_prior_or_on(session, "PLN", today)
                    item_price = (item_price * rate).quantize(Decimal("1.00"))
            except Exception:
                pass
        elif target_currency == "EUR":
            attrs = item.attrs or {}
            eur = (
                attrs.get("price_eur")
                or (attrs.get("prices") or {}).get("EUR")
                or (attrs.get("wizard_frames", {}).get(mp_key) or {}).get("price")
            )
            if eur:
                item_price = Decimal(str(eur))

        # Defensive ensure marketplace row exists in DB to prevent foreign key violation
        mp_db = session.get(Marketplace, mp_key)
        if not mp_db:
            m_title = getattr(mp_cfg, "title", mp_key)
            m_country = getattr(mp_cfg, "country", "CZ")
            m_mode = getattr(mp_cfg, "access_mode", "DIRECT")
            mp_db = Marketplace(
                key=mp_key,
                title=m_title,
                country=m_country,
                currency=currency,
                access_mode=m_mode,
                capabilities={},
                enabled=False,
                config_hash="runtime_sync",
            )
            session.add(mp_db)
            session.flush()

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key=mp_key,
            status=ListingStatus.DRAFT.value,
            title=item.title[:60] if item.title else item_code,
            description=item.description or "",
            category_key=cat_key,
            category_path=cat_path,
            category_auto=True,
            price=item_price,
            currency=currency,
            price_base=item_price,
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

    # If marketplace-relevant attributes actually changed on a published listing, trigger sync
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
    """Delete listing record from system safely, unlinking FKs."""
    listing = session.get(Listing, listing_id)
    if listing:
        from nextlife.storage.models import Deal, Thread
        session.execute(update(Deal).where(Deal.listing_id == listing_id).values(listing_id=None))
        session.execute(update(Thread).where(Thread.listing_id == listing_id).values(listing_id=None))
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
        session=session, actor="USER", action="BATCH_PUBLISH_STARTED", entity="BATCH",
        entity_id=batch_id, data={"count": len(approved), "item_code": item_code},
    )
    session.flush()
    return batch_id


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
