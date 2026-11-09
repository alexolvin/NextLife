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


from nextlife.services.listings_ops import (
    ListingValidationError,
    approve_listing,
    check_and_enqueue_expired_relistings,
    get_listings_counts,
    set_listing_free,
    unapprove_listing,
    update_listing,
)


def _get_marketplace_cfg(key: str) -> Any | None:
    cfg = load_config()
    return cfg.marketplaces.get(key)


def get_marketplace_base_url(key: str) -> str | None:
    mp = _get_marketplace_cfg(key)
    return str(mp.base_url).strip() if (mp and getattr(mp, "base_url", None)) else None


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

    # Collect item photo ids with cover photo first (§5.4)
    from nextlife.services.listings_photos import get_item_ordered_photo_ids
    photo_ids = get_item_ordered_photo_ids(session, item_code)

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
        mp_cfg = config.marketplaces[mp_key]
        currency = getattr(mp_cfg, "currency", "CZK")
        from nextlife.services.localize import get_marketplace_language, translate_listing_content
        target_lang = getattr(mp_cfg, "language", None) or get_marketplace_language(mp_cfg, mp_key)
        source_lang = (item.description_lang or "cs").strip().lower()

        item_currency = (item.target_currency or "CZK").strip().upper()
        target_currency = currency.strip().upper()

        if existing:
            if existing.status == ListingStatus.DRAFT.value and existing.lang != target_lang:
                e_title, e_desc = translate_listing_content(
                    item.title or item_code, item.description or "", target_lang=target_lang, source_lang=source_lang
                )
                existing.title = e_title[:60]
                existing.description = e_desc
                existing.lang = target_lang
            else:
                if not existing.title:
                    existing.title = item.title[:60] if item.title else item_code
                if not existing.description:
                    existing.description = item.description or ""
                if not existing.lang:
                    existing.lang = target_lang

            # Fix currency conversion for DRAFT/APPROVED listings if mismatched or uncalculated
            if existing.status in (ListingStatus.DRAFT.value, "APPROVED"):
                from nextlife.money.money import convert_amount
                from datetime import date
                today = date.today()
                if existing.currency != target_currency or (target_currency != item_currency and existing.price == item.target_price):
                    try:
                        existing.price = convert_amount(session, price_val, item_currency, target_currency, today)
                        existing.price_base = price_val
                        existing.currency = target_currency
                    except Exception:
                        pass

            if not existing.price:
                existing.price = price_val
                existing.price_base = price_val
            if not existing.photo_ids:
                existing.photo_ids = photo_ids
            if not (existing.category_key or existing.category_path):
                existing.category_key, existing.category_path = cat_key, cat_path
            created_or_existing.append(existing)
            continue

        item_price = price_val
        if target_currency != item_currency and item_price:
            from datetime import date
            from nextlife.money.money import convert_amount
            today = date.today()
            try:
                item_price = convert_amount(session, item_price, item_currency, target_currency, today)
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

        if target_lang != source_lang:
            l_title, l_desc = translate_listing_content(
                item.title or item_code, item.description or "", target_lang=target_lang, source_lang=source_lang
            )
        else:
            l_title = item.title[:60] if item.title else item_code
            l_desc = item.description or ""

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key=mp_key,
            status=ListingStatus.DRAFT.value,
            title=l_title[:60] if l_title else item_code,
            description=l_desc,
            category_key=cat_key,
            category_path=cat_path,
            category_auto=True,
            price=item_price,
            currency=currency,
            price_base=item_price,
            lang=target_lang,
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
    listing.status = ListingStatus.UPDATING.value
    listing.last_error_text = None
    listing.last_error_hint = "Выполняется снятие с площадки..."
    session.flush()

    return {"supported": True, "job_id": job.id, "status": "QUEUED"}


def publish_listing_now(session: Session, listing_id: str) -> dict[str, Any]:
    """Publish single listing immediately (§6.3)."""
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ListingValidationError(f"Объявление {listing_id} не найдено")

    listing.status = ListingStatus.QUEUED.value
    listing.last_error_code = None
    listing.last_error_text = None
    listing.last_error_hint = None
    job = create_job(
        session=session,
        kind="PUBLISH_LISTING",
        payload={
            "listing_id": listing.id,
            "item_code": listing.item_code,
            "marketplace_key": listing.marketplace_key,
        },
        idempotency_key=f"publish:{listing.id}:{uuid.uuid4()}",
    )
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


class ListingDeletionForbiddenError(NextLifeError):
    """Raised when attempting to delete a listing that is live on a marketplace."""
    pass


def delete_listing(session: Session, listing_id: str) -> None:
    """Delete listing record from system safely, unlinking FKs.

    Closed-loop guard: never allow deletion of a listing while it is published
    or active on a marketplace (PUBLISHED, UPDATING, UNPUBLISH_FAILED).
    """
    listing = session.get(Listing, listing_id)
    if listing:
        live_statuses = {
            ListingStatus.PUBLISHED.value,
            ListingStatus.UPDATING.value,
            ListingStatus.UNPUBLISH_FAILED.value,
        }
        if listing.status in live_statuses:
            raise ListingDeletionForbiddenError(
                f"Нельзя удалить объявление #{listing_id} ({listing.marketplace_key}): "
                f"оно активно на площадке (статус {listing.status}). "
                f"Сначала снимите его с размещения."
            )
        if listing.external_url and listing.status != ListingStatus.ARCHIVED.value:
            from nextlife.services.listings_reconcile import check_url_is_live
            if check_url_is_live(listing.external_url):
                raise ListingDeletionForbiddenError(
                    f"Нельзя удалить объявление #{listing_id} ({listing.marketplace_key}): "
                    f"сетевая проверка показала, что объявление всё ещё доступно на площадке ({listing.external_url}). "
                    f"Сначала снимите его с размещения."
                )
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
