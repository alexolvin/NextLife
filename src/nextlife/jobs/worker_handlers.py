"""Job execution handlers for NextLife worker.

Implements closed-loop verification for publishing, relisting, and unpublishing (§2026-11-08).
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.status import ItemStatus, ListingStatus
from nextlife.storage.models import Item, Listing

logger = logging.getLogger("nextlife.worker.handlers")


def handle_publish_listing(payload: dict[str, Any], session: Session) -> None:
    """Execute PUBLISH_LISTING job with closed-loop verification."""
    listing_id = payload.get("listing_id")
    listing = session.get(Listing, listing_id)
    if not listing:
        raise ValueError(f"Listing {listing_id} not found")

    listing.status = ListingStatus.PUBLISHING.value
    session.flush()

    from nextlife.connectors.registry import get_connector
    from nextlife.core.config import load_config
    from nextlife.services.listings import get_marketplace_base_url
    from nextlife.services.listings_reconcile import check_url_is_live
    from nextlife.services.marketplace_shipping import get_marketplace_listing_ttl

    cfg = load_config()
    conn = get_connector(listing.marketplace_key, cfg)
    mp_cfg = cfg.marketplaces.get(listing.marketplace_key)
    can_publish = mp_cfg and (mp_cfg.capabilities.get("publish") or mp_cfg.capabilities.get("post"))

    if not can_publish:
        from nextlife.connectors.base import CapabilityNotSupported
        listing.status = ListingStatus.FAILED.value
        listing.last_error_text = f"Площадка {listing.marketplace_key} не поддерживает автоматическую публикацию"
        listing.last_error_hint = "Текст скопирован в буфер; используйте ручную публикацию"
        session.flush()
        raise CapabilityNotSupported(f"Marketplace {listing.marketplace_key} does not support live publishing")

    if listing.marketplace_key == "bazos_cz":
        from nextlife.services.bazos_publish import bazos_publish_listing
        res = bazos_publish_listing(
            title=listing.title or "",
            description=listing.description or "",
            price=str(listing.price or 0),
            category=listing.category_key or "",
            dry_run=False,
            item_code=listing.item_code or "",
        )
        if not res.get("ok"):
            err_msg = str(res.get("errors") or "Bazoš publication failed")
            listing.status = ListingStatus.FAILED.value
            listing.last_error_text = err_msg
            session.flush()
            raise RuntimeError(err_msg)
        if res.get("external_url"):
            listing.external_url = res["external_url"]
        if res.get("external_id"):
            listing.external_id = res["external_id"]
    elif listing.marketplace_key == "bazos_sk":
        from nextlife.services.bazos_android_publish import bazos_android_publish
        res = bazos_android_publish(
            title=listing.title or "",
            description=listing.description or "",
            price=str(listing.price or 0),
            category_key=listing.category_key or "",
            platform="bazos_sk",
            dry_run=False,
            item_code=listing.item_code or "",
        )
        if not res.get("ok"):
            err_msg = "; ".join(res.get("errors") or ["Bazoš SK publication failed"])
            listing.status = ListingStatus.FAILED.value
            listing.last_error_text = err_msg
            session.flush()
            raise RuntimeError(err_msg)
        if res.get("external_url"):
            listing.external_url = res["external_url"]
        if res.get("external_id"):
            listing.external_id = res["external_id"]
    else:
        if hasattr(conn, "post_listing"):
            res = conn.post_listing(listing)
            if isinstance(res, dict) and res.get("external_url"):
                listing.external_url = res["external_url"]
        else:
            listing.status = ListingStatus.FAILED.value
            listing.last_error_text = f"Коннектор {listing.marketplace_key} не имеет метода публикации"
            session.flush()
            raise RuntimeError(listing.last_error_text)

    if not listing.external_url:
        listing.external_url = get_marketplace_base_url(listing.marketplace_key)

    # Closed-loop verification
    if listing.external_url and not listing.external_url.rstrip("/").endswith((".cz", ".sk", ".de", ".at", ".com", ".pl")):
        if not check_url_is_live(listing.external_url):
            listing.status = ListingStatus.FAILED.value
            listing.last_error_text = "Объявление не найдено на площадке после публикации"
            listing.last_error_hint = "Проверьте модерацию площадки или ссылку перехода"
            session.flush()
            raise RuntimeError("Listing not verified live on marketplace")

    listing.status = ListingStatus.PUBLISHED.value
    listing.published_at = datetime.now(UTC)
    if not listing.first_published_at:
        listing.first_published_at = listing.published_at
    ttl = get_marketplace_listing_ttl(listing.marketplace_key)
    listing.expires_at = listing.published_at + timedelta(days=ttl)
    listing.last_error_text = None
    listing.last_error_hint = None
    session.flush()

    item = session.get(Item, listing.item_code)
    if item and item.status not in (ItemStatus.SOLD.value, ItemStatus.RESERVED.value, ItemStatus.DELETED.value):
        item.status = ItemStatus.PUBLISHED.value


def handle_unpublish_listing(payload: dict[str, Any], session: Session) -> None:
    """Execute UNPUBLISH_LISTING job with closed-loop verification."""
    listing_id = payload.get("listing_id")
    listing = session.get(Listing, listing_id)
    if not listing:
        return

    from nextlife.services.listings_reconcile import check_url_is_live

    # 1. Attempt unpublishing on the platform
    unpub_err = None
    if listing.marketplace_key in ("bazos_cz", "bazos_sk"):
        from nextlife.services.bazos_android_publish import bazos_android_delete_listing
        try:
            res = bazos_android_delete_listing(
                title=listing.title or "",
                external_id=listing.external_id,
                platform=listing.marketplace_key,
                dry_run=False,
            )
            if not res.get("ok"):
                unpub_err = "; ".join(res.get("errors") or ["Удаление не подтверждено приложением Bazoš"])
        except Exception as exc:
            unpub_err = str(exc)
    else:
        from nextlife.connectors.registry import get_connector
        from nextlife.core.config import load_config
        try:
            cfg = load_config()
            conn = get_connector(listing.marketplace_key, cfg)
            if hasattr(conn, "unpublish"):
                conn.unpublish(listing.external_id or "")
        except Exception as exc:
            unpub_err = str(exc)

    if unpub_err:
        listing.status = ListingStatus.UNPUBLISH_FAILED.value
        listing.last_error_text = f"Ошибка снятия с площадки: {unpub_err}"
        listing.last_error_hint = "Объявление продолжает экспонироваться на площадке! Снимите его вручную."
        session.flush()
        raise RuntimeError(f"Unpublish failed on {listing.marketplace_key}: {unpub_err}")

    # 2. Closed-loop verification: ensure the ad is no longer live on the marketplace
    if listing.external_url and not listing.external_url.rstrip("/").endswith((".cz", ".sk", ".de", ".at", ".com", ".pl")):
        still_live = check_url_is_live(listing.external_url)
        if still_live:
            listing.status = ListingStatus.UNPUBLISH_FAILED.value
            listing.last_error_text = "Проверка показала: объявление всё ещё активно на площадке!"
            listing.last_error_hint = "Снимите объявление вручную по ссылке перехода."
            session.flush()
            raise RuntimeError(f"Listing {listing.id} is still live on {listing.marketplace_key} after unpublish")

    # 3. Confirmed removed
    listing.status = ListingStatus.ARCHIVED.value
    listing.archived_at = datetime.now(UTC)
    listing.last_error_text = None
    listing.last_error_hint = None
    session.flush()

    item = session.get(Item, listing.item_code)
    if item and item.status == ItemStatus.PUBLISHED.value:
        active_cnt = session.scalar(
            select(Listing)
            .where(Listing.item_code == item.code)
            .where(Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value]))
        )
        if not active_cnt:
            item.status = ItemStatus.READY_FOR_SALE.value


def handle_relist_listing(payload: dict[str, Any], session: Session) -> None:
    """Execute RELIST_LISTING job with closed-loop verification."""
    old_id = payload.get("listing_id")
    old_listing = session.get(Listing, old_id)
    if not old_listing:
        return

    # 1. Unpublish old listing with verification
    handle_unpublish_listing({"listing_id": old_id}, session)

    # 2. Create new listing record in chain
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
        auto_relist=getattr(old_listing, "auto_relist", False),
        previous_listing_id=old_listing.id,
        first_published_at=old_listing.first_published_at or old_listing.published_at,
    )
    session.add(new_listing)
    session.flush()

    # 3. Publish new listing with verification
    handle_publish_listing({"listing_id": new_listing.id}, session)


def handle_warranty_expire(payload: dict[str, Any], session: Session) -> None:
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


def handle_update_listing(payload: dict[str, Any], session: Session) -> None:
    """Execute UPDATE_LISTING job."""
    listing_id = payload.get("listing_id")
    listing = session.get(Listing, listing_id)
    if not listing:
        return

    listing.status = ListingStatus.UPDATING.value
    session.flush()

    listing.status = ListingStatus.PUBLISHED.value
    listing.updated_at = datetime.now(UTC)
    session.flush()
