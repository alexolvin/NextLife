"""Unified search service across items, listings, and messages (TZ §9)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nextlife.core.text import normalize_search_text
from nextlife.storage.models import Item, Listing, Message, Thread


def search_unified(
    session: Session,
    query: str,
    scope: str = "all",
    limit: int = 20,
) -> dict[str, Any]:
    """Search items, listings, and messages with normalized query matching.

    Matches each word of query with AND condition.
    Ranks exact code match first, prefix match second, substring match third.
    """
    norm_q = normalize_search_text(query)
    words = [w for w in norm_q.split() if w]
    if not words:
        return {
            "query": query,
            "scope": scope,
            "total": 0,
            "items": [],
            "listings": [],
            "messages": [],
            "results": [],
        }

    items_res: list[dict[str, Any]] = []
    listings_res: list[dict[str, Any]] = []
    messages_res: list[dict[str, Any]] = []

    # 1. Search items
    if scope in ("all", "items"):
        stmt = select(Item)
        for w in words:
            w_pattern = f"%{w}%"
            stmt = stmt.where(
                or_(
                    Item.search_text.ilike(w_pattern),
                    Item.code.ilike(w_pattern),
                    Item.title.ilike(w_pattern),
                )
            )
        db_items = session.scalars(stmt.limit(50)).all()
        for item in db_items:
            norm_code = normalize_search_text(item.code)
            norm_title = normalize_search_text(item.title)
            score = 10
            if norm_q == norm_code:
                score = 100
            elif norm_title.startswith(norm_q):
                score = 50
            elif norm_code.startswith(norm_q):
                score = 40

            items_res.append(
                {
                    "type": "item",
                    "id": item.code,
                    "code": item.code,
                    "title": item.title,
                    "status": item.status,
                    "storage_location": item.storage_location,
                    "price": str(item.price_estimate_median) if item.price_estimate_median else None,
                    "url": f"/ui/stock/{item.code}",
                    "score": score,
                }
            )
        items_res.sort(key=lambda x: x["score"], reverse=True)

    # 2. Search listings
    if scope in ("all", "listings"):
        stmt_l = select(Listing)
        for w in words:
            w_pat = f"%{w}%"
            stmt_l = stmt_l.where(
                or_(
                    Listing.title.ilike(w_pat),
                    Listing.description.ilike(w_pat),
                    Listing.item_code.ilike(w_pat),
                    Listing.marketplace_key.ilike(w_pat),
                )
            )
        db_listings = session.scalars(stmt_l.limit(50)).all()
        for listing_item in db_listings:
            norm_title = normalize_search_text(listing_item.title or "")
            norm_code = normalize_search_text(listing_item.item_code)
            score = 10
            if norm_q == norm_code:
                score = 100
            elif norm_title.startswith(norm_q):
                score = 50
            listings_res.append(
                {
                    "type": "listing",
                    "id": listing_item.id,
                    "item_code": listing_item.item_code,
                    "title": listing_item.title or f"Объявление #{listing_item.item_code}",
                    "marketplace_key": listing_item.marketplace_key,
                    "status": listing_item.status,
                    "price": str(listing_item.price),
                    "currency": listing_item.currency,
                    "url": f"/ui/listings/{listing_item.id}",
                    "score": score,
                }
            )
        listings_res.sort(key=lambda x: x["score"], reverse=True)

    # 3. Search messages
    if scope in ("all", "messages"):
        stmt_m = select(Message).join(Thread, Message.thread_id == Thread.id)
        for w in words:
            w_pat = f"%{w}%"
            stmt_m = stmt_m.where(
                or_(
                    Message.text_original.ilike(w_pat),
                    Message.text_ru.ilike(w_pat),
                    Thread.counterparty.ilike(w_pat),
                )
            )
        db_messages = session.scalars(stmt_m.limit(50)).all()
        for m in db_messages:
            preview = m.text_ru or m.text_original
            if len(preview) > 80:
                preview = preview[:77] + "..."
            messages_res.append(
                {
                    "type": "message",
                    "id": m.id,
                    "thread_id": m.thread_id,
                    "title": preview,
                    "direction": m.direction,
                    "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                    "url": f"/ui/messages?thread_id={m.thread_id}",
                    "score": 10,
                }
            )
        messages_res.sort(key=lambda x: x["score"], reverse=True)

    if scope == "all":
        # Grouped presentation: top 5 from each category, plus combined results
        combined = items_res[:limit] + listings_res[:limit] + messages_res[:limit]
        combined.sort(key=lambda x: x["score"], reverse=True)
        return {
            "query": query,
            "scope": scope,
            "total": len(items_res) + len(listings_res) + len(messages_res),
            "items": items_res[:5],
            "listings": listings_res[:5],
            "messages": messages_res[:5],
            "results": combined[:limit],
        }
    elif scope == "items":
        return {
            "query": query,
            "scope": scope,
            "total": len(items_res),
            "items": items_res[:limit],
            "listings": [],
            "messages": [],
            "results": items_res[:limit],
        }
    elif scope == "listings":
        return {
            "query": query,
            "scope": scope,
            "total": len(listings_res),
            "items": [],
            "listings": listings_res[:limit],
            "messages": [],
            "results": listings_res[:limit],
        }
    else:  # messages
        return {
            "query": query,
            "scope": scope,
            "total": len(messages_res),
            "items": [],
            "listings": [],
            "messages": messages_res[:limit],
            "results": messages_res[:limit],
        }
