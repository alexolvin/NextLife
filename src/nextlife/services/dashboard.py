"""Dashboard and Attention service aggregating 10 prioritized action cards (TZ §10).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nextlife.core.status import ItemStatus, ListingStatus
from nextlife.storage.models import BuyCandidate, Deal, Item, Listing, Message, Thread


def get_attention_cards(session: Session) -> list[dict[str, Any]]:
    """Compute 10 prioritized attention cards per TZ §10."""
    now = datetime.now(UTC)
    three_days_ago = now - timedelta(days=3)
    seven_days = now.date() + timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)

    # 1. Новые сообщения покупателей (unread incoming messages)
    subq = (
        select(Message.thread_id, func.max(Message.created_at).label("last_at"))
        .group_by(Message.thread_id)
        .subquery()
    )
    unread_threads = session.scalars(
        select(Thread)
        .join(subq, Thread.id == subq.c.thread_id)
        .join(Message, (Message.thread_id == Thread.id) & (Message.created_at == subq.c.last_at))
        .where(Message.direction == "IN")
    ).all()
    count_1 = len(unread_threads)

    # 2. Ожидают отправки (с местом на складе)
    shipment_deals = session.scalars(
        select(Deal).where(
            Deal.status.in_(["PAID", "AWAITING_SHIPMENT", "READY_TO_SHIP"]),
            Deal.closed_at.is_(None),
        )
    ).all()
    count_2 = len(shipment_deals)

    # 3. Ожидают подтверждения оплаты
    payment_deals = session.scalars(
        select(Deal).where(
            Deal.status.in_(["AWAITING_PAYMENT", "PENDING_CONFIRMATION", "RESERVED"]),
            Deal.closed_at.is_(None),
        )
    ).all()
    count_3 = len(payment_deals)

    # 4. Истекающие резервы
    reserved_items = session.scalars(
        select(Item).where(Item.status == ItemStatus.RESERVED.value)
    ).all()
    count_4 = len(reserved_items)

    # 5. Ошибки размещения и «Не снято»
    error_listings = session.scalars(
        select(Listing).where(
            Listing.status.in_([ListingStatus.FAILED.value, ListingStatus.UNPUBLISH_FAILED.value])
        )
    ).all()
    count_5 = len(error_listings)

    # 6. Утверждены, не размещены
    approved_listings = session.scalars(
        select(Listing).where(Listing.status == ListingStatus.APPROVED.value)
    ).all()
    count_6 = len(approved_listings)

    # 7. Застоявшиеся объявления (> 14 дней без сообщений)
    stale_listings = session.scalars(
        select(Listing).where(
            Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value]),
            Listing.first_published_at <= fourteen_days_ago,
        )
    ).all()
    count_7 = len(stale_listings)

    # 8. Новые предметы без оценки > 3 дней
    unestimated_items = session.scalars(
        select(Item).where(
            Item.status.in_([ItemStatus.DRAFT.value, "NEW"]),
            Item.price_estimate_median.is_(None),
            Item.created_at <= three_days_ago,
        )
    ).all()
    count_8 = len(unestimated_items)

    # 9. Гарантия истекает через 7 дней
    warranty_items = session.scalars(
        select(Item).where(
            Item.warranty_until.is_not(None),
            Item.warranty_until >= now.date(),
            Item.warranty_until <= seven_days,
        )
    ).all()
    count_9 = len(warranty_items)

    # 10. Выгодные комплекты (profit >= 25%)
    all_candidates = session.scalars(select(BuyCandidate)).all()
    profitable_kits = []
    for c in all_candidates:
        v = c.verdict
        if isinstance(v, str) and v.strip():
            try:
                v = json.loads(v)
            except Exception:
                v = {}
        if isinstance(v, dict) and v.get("kit_analysis", {}).get("is_profitable"):
            profitable_kits.append(c)
    count_10 = len(profitable_kits)

    cards = [
        {"order": 1, "title": "Новые сообщения покупателей", "count": count_1, "url": "/ui/messages?filter=unread", "icon": "💬", "color": "#2563eb"},
        {"order": 2, "title": "Ожидают отправки", "count": count_2, "url": "/ui/stock?group=on_sale", "icon": "📦", "color": "#ea580c"},
        {"order": 3, "title": "Ожидают подтверждения оплаты", "count": count_3, "url": "/ui/stock?group=sold", "icon": "💳", "color": "#16a34a"},
        {"order": 4, "title": "Истекающие резервы", "count": count_4, "url": "/ui/stock?group=on_sale", "icon": "⏳", "color": "#d97706"},
        {"order": 5, "title": "Ошибки размещения и «Не снято»", "count": count_5, "url": "/ui/listings?filter=errors", "icon": "⚠️", "color": "#dc2626"},
        {"order": 6, "title": "Утверждены, не размещены", "count": count_6, "url": "/ui/listings?filter=approved", "icon": "📋", "color": "#2563eb"},
        {"order": 7, "title": "Застоявшиеся объявления (> 14 дн.)", "count": count_7, "url": "/ui/listings?sort=first_published_asc", "icon": "⏱", "color": "#64748b"},
        {"order": 8, "title": "Новые предметы без оценки > 3 дн.", "count": count_8, "url": "/ui/stock?group=new", "icon": "🏷️", "color": "#7c3aed"},
        {"order": 9, "title": "Гарантия истекает через 7 дней", "count": count_9, "url": "/ui/stock?group=sold", "icon": "🛡️", "color": "#0284c7"},
        {"order": 10, "title": "Выгодные комплекты", "count": count_10, "url": "/ui/buy?offer_type=kit", "icon": "🎁", "color": "#059669"},
    ]

    return [c for c in cards if c["count"] > 0]


def get_dashboard_metrics(session: Session | None) -> dict[str, Any]:
    """Legacy backward-compatible metrics dictionary."""
    if session is None:
        return {}
    cards = get_attention_cards(session)
    return {c["title"]: {"count": c["count"], "url": c["url"]} for c in cards}
