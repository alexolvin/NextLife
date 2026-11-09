"""Склад (Warehouse) service — ТЗ «Склад» вместо «Ленты», этап 1.

Единый сервис со всеми операциями Склада. Web UI, ``/api/stock/*`` и
``/api/oc/stock/*`` вызывают только этот модуль. Прямая запись полей Склада
из маршрутов запрещена (§1.4).

«Предмет продажи» — строка таблицы ``item``. Код предмета — ``item.code``.
Таблица ``lot`` — внутренняя запись 1:1 с тем же кодом; поля
``lot.price_gross``, ``lot.currency``, ``lot.notes`` пишутся только этим
сервисом синхронно с ``item`` в одной транзакции.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.core.status import ItemStatus, ListingStatus, validate_transition
from nextlife.core.text import build_item_search_text, normalize_search_text
from nextlife.media.ops import delete_photo_and_derived
from nextlife.obs.audit import record_audit_event
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.lot_delete import LotDeletionBlockedError, can_delete_lot
from nextlife.services.listings import get_marketplace_base_url
from nextlife.services.listings_sort import sort_listings_by_status
from nextlife.services.unpublish import unpublish_listings_now
from nextlife.storage.models import Deal, Item, ItemPhoto, Listing, Lot

# По умолчанию категория нового предмета (уточняется в мастере).
_DEFAULT_CATEGORY = "general"

# Правило 1: изменение этих полей у AWAITING_APPROVAL/APPROVED сбрасывает в DRAFT.
_APPROVAL_FIELDS = (
    "title",
    "description",
    "description_lang",
    "usage",
    "specs",
    "target_price",
    "target_currency",
    "price_eur",
    "category_key",
)
# Правило 2: изменение этих полей статус не меняет.
_NEUTRAL_FIELDS = ("notes", "storage_location", "warranty_months", "warranty_terms")
# Поля доставки (item) + политика (lot).
_DELIVERY_FIELDS = (
    "weight_g",
    "packed_weight_g",
    "dim_l_mm",
    "dim_w_mm",
    "dim_h_mm",
)

# Статусы, которые считаются «объявление на площадке» для счётчика (§2.3.3).
_ACTIVE_LISTING_STATUSES = ("PUBLISHED", "PARTIALLY_PUBLISHED", "UPDATING", "UNPUBLISH_FAILED")

# status_group -> набор статусов (ТЗ §4.1.2).
_STATUS_GROUPS: dict[str, frozenset[str] | None] = {
    "all": frozenset({
        "NEW", "ESTIMATED", "PHOTOGRAPHED", "DESCRIBED", "READY_FOR_SALE",
        "PUBLISHED", "PARTIALLY_PUBLISHED", "RESERVED", "DRAFT", "RESEARCHED",
        "MEDIA_READY", "AWAITING_APPROVAL", "APPROVED"
    }),
    "new": frozenset({"NEW", "ESTIMATED"}),
    "prep": frozenset({"PHOTOGRAPHED", "DESCRIBED", "MEDIA_READY", "DRAFT", "RESEARCHED"}),
    "drafts": frozenset({"DRAFT", "RESEARCHED", "MEDIA_READY", "AWAITING_APPROVAL", "APPROVED", "PHOTOGRAPHED", "DESCRIBED"}),
    "ready": frozenset({"READY_FOR_SALE", "APPROVED", "AWAITING_APPROVAL"}),
    "on_sale": frozenset({"PUBLISHED", "PARTIALLY_PUBLISHED", "RESERVED"}),
    "sold": frozenset({"SOLD", "IN_WARRANTY", "RETURNED"}),
    "archive": frozenset({"ARCHIVED", "DISPOSED"}),
}


class StockError(NextLifeError):
    """Base exception for stock operations."""


class StockItemNotFoundError(StockError):
    """Предмет не найден (404)."""


class StockDeletionNeedsConfirmationError(StockError):
    """Нужно подтверждение снятия объявлений (409)."""


class StockValidationError(StockError):
    """Ошибка полей (422)."""


def _get_item(session: Session, code: str) -> Item:
    item = session.get(Item, code)
    if not item:
        raise StockItemNotFoundError(f"Предмет #{code} не найден.")
    return item


def _active_listings(session: Session, item_code: str) -> list[Listing]:
    return list(
        session.scalars(
            select(Listing).where(
                Listing.item_code == item_code,
                Listing.status.in_(_ACTIVE_LISTING_STATUSES),
            )
        ).all()
    )


def _listings_count(session: Session, item_code: str) -> int:
    return len(_active_listings(session, item_code))


def _photo_entries(session: Session, item_code: str) -> list[dict[str, Any]]:
    photos = session.scalars(
        select(ItemPhoto).where(ItemPhoto.item_code == item_code)
    ).all()
    cover_id = None
    # Обложка: attrs.cover_photo_id (если есть).
    return [
        {
            "id": p.id,
            "role": p.role,
            "path": p.path,
            "ai_generated": p.ai_generated,
            "is_cover": p.id == cover_id,
        }
        for p in photos
    ]


def get_stock_item(session: Session, code: str) -> dict[str, Any]:
    """Полная карточка предмета: поля §1.1, фото, объявления, оценка, гарантия, сделка."""
    item = _get_item(session, code)
    lot = session.scalar(select(Lot).where(Lot.code == code))
    deal = session.scalar(
        select(Deal).join(Lot, Deal.lot_id == Lot.id).where(Lot.code == code).order_by(Deal.created_at.desc())
    )
    listings = session.scalars(
        select(Listing).where(Listing.item_code == code).order_by(Listing.published_at.desc())
    ).all()
    now = datetime.now(UTC)
    days_left = None
    if item.warranty_until:
        days_left = max(0, (item.warranty_until - now.date()).days)

    return {
        "code": item.code,
        "title": item.title,
        "category_key": item.category_key,
        "status": item.status,
        "description": item.description,
        "description_lang": item.description_lang,
        "usage": item.usage,
        "specs": item.specs,
        "notes": item.notes,
        "storage_location": item.storage_location,
        "target_price": str(item.target_price) if item.target_price is not None else None,
        "target_currency": item.target_currency,
        "price_eur": str(item.price_eur) if item.price_eur is not None else (str(item.attrs.get("price_eur")) if item.attrs and item.attrs.get("price_eur") else None),
        "warranty_months": item.warranty_months,
        "warranty_terms": item.warranty_terms,
        "warranty_until": item.warranty_until.isoformat() if item.warranty_until else None,
        "warranty_days_left": days_left,
        "price_estimate_low": str(item.price_estimate_low) if item.price_estimate_low is not None else None,
        "price_estimate_median": str(item.price_estimate_median) if item.price_estimate_median is not None else None,
        "price_estimate_high": str(item.price_estimate_high) if item.price_estimate_high is not None else None,
        "price_estimate_currency": item.price_estimate_currency or "CZK",
        "price_estimate_at": item.price_estimate_at.isoformat() if item.price_estimate_at else None,
        "deal": {
            "id": deal.id,
            "status": deal.status,
            "agreed_price": str(deal.agreed_price),
            "currency": deal.currency,
            "buyer": (deal.party.name if deal.party else "Покупатель"),
            "delivery_method": deal.delivery_method,
            "payment_method": deal.payment_method,
            "sold_at": deal.created_at.isoformat() if deal.created_at else None,
        } if deal else None,
        "weight_g": item.weight_g,
        "packed_weight_g": item.packed_weight_g,
        "dim_l_mm": item.dim_l_mm,
        "dim_w_mm": item.dim_w_mm,
        "dim_h_mm": item.dim_h_mm,
        "shipping_policy": lot.shipping_policy if lot else "BUYER_PAYS",
        "photos": _photo_entries(session, code),
        "listings": sort_listings_by_status([
            {
                "id": listing_row.id,
                "marketplace_key": listing_row.marketplace_key,
                "status": listing_row.status,
                "title": listing_row.title,
                "category_key": listing_row.category_key,
                "category_path": listing_row.category_path,
                "category_auto": listing_row.category_auto,
                "price": str(listing_row.price),
                "currency": listing_row.currency,
                "lang": listing_row.lang,
                "is_approved": listing_row.status == ListingStatus.APPROVED.value,
                "external_url": listing_row.external_url or (get_marketplace_base_url(listing_row.marketplace_key) if listing_row.status in (ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value) else None),
                "last_error_text": listing_row.last_error_text,
                "last_error_hint": listing_row.last_error_hint,
                "published_at": listing_row.published_at.isoformat() if listing_row.published_at else None,
            }
            for listing_row in listings
        ]),
        "listings_count": _listings_count(session, code),
    }


def list_stock(
    session: Session,
    status_group: str = "all",
    q: str | None = None,
    sort: str = "recent",
) -> list[dict[str, Any]]:
    """Список предметов без DELETED и CANCELLED с фильтрацией и сортировкой (ТЗ §4.1)."""
    if status_group not in _STATUS_GROUPS:
        raise StockValidationError(f"Неизвестная группа статусов: {status_group}")
    query = select(Item).where(Item.status.notin_(["DELETED", "CANCELLED"]))
    statuses = _STATUS_GROUPS[status_group]
    if statuses is not None:
        query = query.where(Item.status.in_(statuses))
    if q:
        norm_words = normalize_search_text(q).split()
        for w in norm_words:
            w_pat = f"%{w}%"
            query = query.where(
                or_(
                    Item.search_text.ilike(w_pat),
                    Item.code.ilike(w_pat),
                    Item.title.ilike(w_pat),
                    Item.storage_location.ilike(w_pat),
                )
            )

    if sort == "oldest":
        query = query.order_by(Item.created_at.asc())
    elif sort == "price_desc":
        query = query.order_by(Item.target_price.desc().nulls_last())
    elif sort == "location":
        query = query.order_by(Item.storage_location.asc().nulls_last())
    else:  # recent
        query = query.order_by(Item.updated_at.desc())

    items = session.execute(query).scalars().all()
    result = []
    now = datetime.now(UTC)
    for it in items:
        list_count = _listings_count(session, it.code)
        photos_count = session.scalar(
            select(func.count(ItemPhoto.id)).where(ItemPhoto.item_code == it.code)
        ) or 0

        # Stages 1..6 booleans for dot indicators
        s1 = bool(it.price_estimate_median or it.target_price or it.status in ("ESTIMATED", "READY_FOR_SALE", "PUBLISHED", "RESERVED", "SOLD"))
        s2 = photos_count > 0 or it.status in ("PHOTOGRAPHED", "DESCRIBED", "READY_FOR_SALE", "PUBLISHED", "RESERVED", "SOLD")
        s3 = bool(it.description or it.status in ("DESCRIBED", "READY_FOR_SALE", "PUBLISHED", "RESERVED", "SOLD"))
        s4 = list_count > 0 or it.status in ("READY_FOR_SALE", "PUBLISHED", "RESERVED", "SOLD")
        s5 = it.status in ("SOLD", "IN_WARRANTY", "RETURNED")
        s6 = it.status == "IN_WARRANTY" or bool(it.warranty_until and it.warranty_until >= now.date())

        price_display = None
        if it.target_price is not None:
            price_display = f"{it.target_price} {it.target_currency or 'CZK'}"
        elif it.price_estimate_median is not None:
            price_display = f"≈ {it.price_estimate_median} {it.price_estimate_currency or 'CZK'}"

        result.append(
            {
                "code": it.code,
                "title": it.title,
                "status": it.status,
                "target_price": str(it.target_price) if it.target_price is not None else None,
                "target_currency": it.target_currency,
                "price_estimate_median": str(it.price_estimate_median) if it.price_estimate_median is not None else None,
                "price_display": price_display,
                "storage_location": it.storage_location,
                "listings_count": list_count,
                "photos_count": photos_count,
                "stages": [s1, s2, s3, s4, s5, s6],
                "created_at": it.created_at,
                "updated_at": it.updated_at,
            }
        )
    return result


def get_stock_group_counts(session: Session) -> dict[str, int]:
    """Calculate counts of items in each of the status groups (ТЗ §4.1.2)."""
    all_items = session.execute(
        select(Item.status).where(Item.status.notin_(["DELETED", "CANCELLED"]))
    ).scalars().all()
    status_counts: dict[str, int] = {}
    for st in all_items:
        status_counts[st] = status_counts.get(st, 0) + 1

    group_counts: dict[str, int] = {}
    for group_key, statuses in _STATUS_GROUPS.items():
        if statuses is None:
            group_counts[group_key] = sum(status_counts.values())
        else:
            group_counts[group_key] = sum(status_counts.get(st, 0) for st in statuses)
    return group_counts


def bulk_update_location(session: Session, codes: list[str], storage_location: str) -> int:
    """Update storage location for multiple items (§4.2)."""
    count = 0
    clean_loc = storage_location.strip() or None
    for code in codes:
        item = session.get(Item, code)
        if item:
            item.storage_location = clean_loc
            item.search_text = build_item_search_text(
                code=item.code,
                title=item.title,
                storage_location=item.storage_location,
                description=item.description,
                specs=item.specs,
                notes=item.notes,
                attrs=item.attrs,
            )
            count += 1
    session.flush()
    return count


def create_stock_item(session: Session, fields: dict[str, Any]) -> dict[str, Any]:
    """Создать предмет Склада: create_item_and_lot() + остальные поля. title обязателен."""
    title = str(fields.get("title") or "").strip()
    if not title:
        raise StockValidationError("Наименование (title) обязательно.")
    category_key = str(fields.get("category_key") or _DEFAULT_CATEGORY).strip() or _DEFAULT_CATEGORY

    price = fields.get("price")
    if price is not None and price != "":
        price = Decimal(str(price))

    created = create_item_and_lot(
        session,
        title=title,
        category_key=category_key,
        price=price,
        currency=fields.get("currency"),
        condition_key=fields.get("condition_key", "used"),
        price_floor=fields.get("price_floor"),
        shipping_policy=fields.get("shipping_policy", "BUYER_PAYS"),
        notes=fields.get("notes"),
    )
    item = session.get(Item, created["item_code"])
    if not item:
        raise StockItemNotFoundError(f"Created item #{created['item_code']} not found.")

    for field in ("description", "description_lang", "usage", "specs", "storage_location", *_DELIVERY_FIELDS):
        if field in fields and fields[field] is not None:
            setattr(item, field, fields[field])
    item.search_text = build_item_search_text(
        code=item.code,
        title=item.title,
        storage_location=item.storage_location,
        description=item.description,
        specs=item.specs,
        notes=item.notes,
        attrs=item.attrs,
    )
    session.flush()

    record_audit_event(
        session=session,
        actor="OWNER",
        action="STOCK_ITEM_CREATED",
        entity="item",
        entity_id=item.code,
        data={"title": title, "category": category_key},
    )
    return get_stock_item(session, item.code)


def update_stock_item(
    session: Session,
    code: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Частичное обновление полей §1.1 с синхронизацией lot.

    Правила:
    1. title/description/description_lang/usage/specs/target_price/target_currency
       у AWAITING_APPROVAL/APPROVED -> DRAFT.
    2. notes/storage_location статус не меняют.
    3. У предмета с активными объявлениями статус не меняется; возвращается
       listings_not_updated: true.
    4. Каждое изменение пишет audit STOCK_ITEM_UPDATED.
    """
    item = _get_item(session, code)
    lot = session.scalar(select(Lot).where(Lot.code == code))

    known = set(_APPROVAL_FIELDS) | set(_NEUTRAL_FIELDS) | set(_DELIVERY_FIELDS) | {"shipping_policy"}
    changed: list[str] = []
    price_or_currency_changed = False

    for field, value in fields.items():
        if field not in known:
            continue
        if field == "shipping_policy":
            if lot is not None and value is not None:
                lot.shipping_policy = value
                changed.append("shipping_policy")
            continue
        current = getattr(item, field)
        if value is None:
            continue
        if field in ("target_price", "price_eur"):
            value = Decimal(str(value)) if value != "" else None
        if current == value:
            continue
        setattr(item, field, value)
        changed.append(field)
        if field in ("target_price", "target_currency"):
            price_or_currency_changed = True

    # Синхронизация lot (пишет только сервис stock, §1).
    if lot is not None:
        if "target_price" in changed or price_or_currency_changed:
            lot.price_gross = item.target_price
        if "target_currency" in changed and item.target_currency:
            lot.currency = item.target_currency
        if "notes" in changed:
            lot.notes = item.notes

    item.search_text = build_item_search_text(
        code=item.code,
        title=item.title,
        storage_location=item.storage_location,
        description=item.description,
        specs=item.specs,
        notes=item.notes,
        attrs=item.attrs,
    )

    # Правило 1: сброс в DRAFT.
    approval_changed = any(f in changed for f in _APPROVAL_FIELDS)
    has_active_listings = _listings_count(session, code) > 0
    if approval_changed and item.status in (
        ItemStatus.AWAITING_APPROVAL.value,
        ItemStatus.APPROVED.value,
    ) and not has_active_listings:
        item.status = ItemStatus.DRAFT.value
        if lot is not None:
            lot.status = ItemStatus.DRAFT.value

    session.flush()

    if changed:
        record_audit_event(
            session=session,
            actor="OWNER",
            action="STOCK_ITEM_UPDATED",
            entity="item",
            entity_id=code,
            data={"fields": changed},
        )

    result = get_stock_item(session, code)
    result["listings_not_updated"] = has_active_listings and approval_changed
    return result


def mark_sold(session: Session, code: str, actor: str = "OWNER") -> dict[str, Any]:
    """Перевод в SOLD и снятие всех объявлений (§1.2, §2.3.1)."""
    item = _get_item(session, code)
    if item.status != ItemStatus.SOLD.value:
        validate_transition(ItemStatus(item.status), ItemStatus.SOLD)
        item.status = ItemStatus.SOLD.value
    lot = session.scalar(select(Lot).where(Lot.code == code))
    if lot is not None:
        lot.status = ItemStatus.SOLD.value

    results = unpublish_listings_now(session, code, actor=actor)
    record_audit_event(
        session=session,
        actor=actor,
        action="STOCK_ITEM_SOLD",
        entity="item",
        entity_id=code,
        data={"unpublish": results},
    )
    session.flush()
    return {"code": code, "status": "SOLD", "unpublish": results}


def delete_preview(session: Session, code: str) -> dict[str, Any]:
    """Что будет удалено: объявления по площадкам, число фото, активная сделка."""
    item = _get_item(session, code)
    listings = _active_listings(session, code)
    photo_count = session.query(ItemPhoto).filter(ItemPhoto.item_code == code).count()
    can_delete, reason = can_delete_lot(session, code)
    return {
        "code": code,
        "title": item.title,
        "listings": [
            {
                "marketplace_key": listing_entry.marketplace_key,
                "external_url": listing_entry.external_url,
                "status": listing_entry.status,
            }
            for listing_entry in listings
        ],
        "listings_count": len(listings),
        "photo_count": photo_count,
        "has_active_deal": not can_delete,
        "active_deal_reason": reason,
    }


from nextlife.services.stock_deletion import StockDeletionUnpublishError, delete_stock_item_closed_loop


def delete_stock_item(
    session: Session,
    code: str,
    confirm_listings: bool,
    confirm_deals: bool = False,
    actor: str = "OWNER",
) -> dict[str, Any]:
    """Удаление предмета Склада (§1.3, §3.4) с Closed-Loop Marketplace Verification."""
    return delete_stock_item_closed_loop(
        session=session,
        code=code,
        confirm_listings=confirm_listings,
        confirm_deals=confirm_deals,
        actor=actor,
    )


def check_item_ready_for_sale(session: Session, code: str) -> tuple[bool, list[str]]:
    """Checklist validation for 'READY_FOR_SALE' (ТЗ §5.5).

    Returns (is_ready, list_of_missing_requirements).
    Requirements:
    1. ≥ 1 photo
    2. Description filled
    3. Weight & dimensions filled
    4. Shipping policy selected
    5. Price set (target_price > 0 or price_estimate_median > 0)
    """
    item = _get_item(session, code)
    lot = session.scalar(select(Lot).where(Lot.code == code))
    photos_count = session.scalar(select(func.count(ItemPhoto.id)).where(ItemPhoto.item_code == code)) or 0

    missing: list[str] = []
    if photos_count < 1:
        missing.append("Загрузите как минимум 1 фото предмета")
    if not item.description or not item.description.strip():
        missing.append("Заполните описание предмета")
    if not item.weight_g or not item.dim_l_mm or not item.dim_w_mm or not item.dim_h_mm:
        missing.append("Укажите вес и габариты для расчета доставки")
    if not lot or not lot.shipping_policy:
        missing.append("Выберите политику доставки (покупатель/продавец)")
    if (item.target_price is None or item.target_price <= 0) and (
        item.price_estimate_median is None or item.price_estimate_median <= 0
    ):
        missing.append("Укажите цену продажи или подтвердите оценку аналогов")

    return len(missing) == 0, missing


def set_item_ready_for_sale(session: Session, code: str) -> tuple[bool, list[str]]:
    """Mark item READY_FOR_SALE if checklist passes, or return missing items (ТЗ §5.5)."""
    ready, missing = check_item_ready_for_sale(session, code)
    if not ready:
        return False, missing
    item = _get_item(session, code)
    item.status = ItemStatus.READY_FOR_SALE.value
    session.flush()
    record_audit_event(
        session=session,
        actor="OWNER",
        action="ITEM_READY_FOR_SALE",
        entity="item",
        entity_id=code,
        data={},
    )
    return True, []


def dispose_item(session: Session, code: str, reason: str = "") -> dict[str, Any]:
    """Dispose unviable item (§5.2): status DISPOSED, archive stock."""
    item = _get_item(session, code)
    item.status = ItemStatus.DISPOSED.value
    unpublish_results = unpublish_listings_now(session, code, actor="OWNER")
    session.flush()
    record_audit_event(
        session=session,
        actor="OWNER",
        action="ITEM_DISPOSED",
        entity="item",
        entity_id=code,
        data={"reason": reason},
    )
    return {"status": "ok", "code": code, "unpublish": unpublish_results}


def return_item_warranty(session: Session, code: str, reason: str = "") -> dict[str, Any]:
    """Mark sold item RETURNED under warranty (§5.7)."""
    item = _get_item(session, code)
    item.status = ItemStatus.RETURNED.value
    session.flush()
    record_audit_event(
        session=session,
        actor="OWNER",
        action="ITEM_RETURNED",
        entity="item",
        entity_id=code,
        data={"reason": reason},
    )
    return {"status": "ok", "code": code}


def save_price_estimate(
    session: Session,
    code: str,
    target_price: Decimal | float | int | str,
    currency: str = "CZK",
    median: Decimal | float | int | str | None = None,
    low: Decimal | float | int | str | None = None,
    high: Decimal | float | int | str | None = None,
    price_eur: Decimal | float | int | str | None = None,
) -> Item:
    """Save price research estimate and set status to ESTIMATED (§5.2)."""
    item = _get_item(session, code)
    item.target_price = Decimal(str(target_price))
    item.target_currency = currency
    if price_eur is not None and str(price_eur).strip() != "":
        item.price_eur = Decimal(str(price_eur))
    if median is not None:
        item.price_estimate_median = Decimal(str(median))
    if low is not None:
        item.price_estimate_low = Decimal(str(low))
    if high is not None:
        item.price_estimate_high = Decimal(str(high))
    item.price_estimate_currency = currency
    item.price_estimate_at = datetime.now(UTC)

    if item.status in ("NEW", ItemStatus.DRAFT.value):
        item.status = "ESTIMATED"
    session.flush()
    return item

