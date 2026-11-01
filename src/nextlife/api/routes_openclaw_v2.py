"""OpenClaw API gateway v2 (TZ §12).

Endpoints:
- Stock: price-research (GET/POST), ready (POST), listings/prepare (POST)
- Listings: list (GET), get/patch (GET/PATCH), approve/unapprove (POST),
  publish-approved (POST), unpublish/relist (POST)
- Jobs: get batch job status (GET)
- Messages: list (GET), suggest (POST), send (POST)
- Buy: search (POST), results (GET), kits/analyze (POST)

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select

from nextlife.api.routes_openclaw import check_openclaw_auth
from nextlife.core.status import ItemStatus, ListingStatus
from nextlife.jobs.manager import create_job
from nextlife.services.buy import analyze_kit, create_buy_search, format_candidates
from nextlife.services.listings import (
    approve_listing,
    publish_approved_listings,
    relist_listing_now,
    unapprove_listing,
    unpublish_listing,
)
from nextlife.services.messages import list_threads, send_reply, suggest_reply
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BuyCandidate, BuyRequest, Item, ItemPhoto, Job, Listing

oc_v2_router = APIRouter(prefix="/api/oc", tags=["openclaw-v2"])
_AUTH = Depends(check_openclaw_auth)


class PatchListingPayload(BaseModel):
    title: str | None = None
    description: str | None = None
    price: float | None = None
    category: str | None = None


class SendMessagePayload(BaseModel):
    text: str


class BuySearchPayload(BaseModel):
    query: str
    currency: str = "CZK"
    max_price: float | None = None


# --- STOCK ENDPOINTS ---
@oc_v2_router.post("/stock/{code}/price-research")
def oc_post_price_research(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        job = create_job(session, kind="PRICE_RESEARCH", payload={"item_code": code})
        session.commit()
        return {"job_id": job.id, "status": "queued"}


@oc_v2_router.get("/stock/{code}/price-research")
def oc_get_price_research(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        return {
            "item_code": item.code,
            "target_price": float(item.target_price) if item.target_price is not None else None,
            "currency": item.target_currency or "CZK",
            "status": item.status,
            "notes": (item.attrs or {}).get("research_notes"),
        }


@oc_v2_router.post("/stock/{code}/ready")
def oc_post_stock_ready(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        photos = session.scalars(select(ItemPhoto).where(ItemPhoto.item_code == code)).all()
        unmet = []
        if len(photos) == 0:
            unmet.append("Нет фотографий")
        if not item.target_price or item.target_price <= 0:
            unmet.append("Не задана цена")
        if not item.title or not item.title.strip():
            unmet.append("Не заполнено название")
        if not item.description or not item.description.strip():
            unmet.append("Не заполнено описание")
        if not item.shipping_policy:
            unmet.append("Не выбрана доставка")

        if unmet:
            raise HTTPException(status_code=409, detail={"error": "Checklist not met", "unmet": unmet})

        item.status = ItemStatus.READY_FOR_SALE.value
        session.commit()
        return {"ok": True, "status": item.status, "code": item.code}


@oc_v2_router.post("/stock/{code}/listings/prepare")
def oc_post_listings_prepare(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    from nextlife.services.listings import prepare_item_listings
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        created = prepare_item_listings(session, item_code=code)
        session.commit()
        return {"ok": True, "created": len(created)}


# --- LISTINGS ENDPOINTS ---
@oc_v2_router.get("/listings")
def oc_get_listings(
    status_filter: str | None = Query(default=None, alias="status"),
    marketplace: str | None = Query(default=None),
    sort: str = Query(default="newest"),
    auth: bool = _AUTH,
) -> list[dict[str, Any]]:
    with get_db_session() as session:
        stmt = select(Listing)
        if status_filter:
            stmt = stmt.where(Listing.status == status_filter)
        if marketplace:
            stmt = stmt.where(Listing.marketplace_key == marketplace)
        stmt = stmt.order_by(desc(Listing.created_at))
        listings = session.scalars(stmt).all()
        return [
            {
                "id": lst.id,
                "item_code": lst.item_code,
                "marketplace": lst.marketplace_key,
                "title": lst.title,
                "price": float(lst.price) if lst.price is not None else None,
                "currency": lst.currency,
                "status": lst.status,
                "url": lst.publish_evidence.get("url") if lst.publish_evidence else None,
            }
            for lst in listings
        ]


@oc_v2_router.get("/listings/{listing_id}")
def oc_get_listing(listing_id: int, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        lst = session.get(Listing, listing_id)
        if not lst:
            raise HTTPException(status_code=404, detail="Listing not found")
        return {
            "id": lst.id,
            "item_code": lst.item_code,
            "marketplace": lst.marketplace_key,
            "title": lst.title,
            "description": lst.description,
            "price": float(lst.price) if lst.price is not None else None,
            "currency": lst.currency,
            "status": lst.status,
            "category": lst.category_raw,
            "publish_evidence": lst.publish_evidence,
        }


@oc_v2_router.patch("/listings/{listing_id}")
def oc_patch_listing(listing_id: int, payload: PatchListingPayload, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        lst = session.get(Listing, listing_id)
        if not lst:
            raise HTTPException(status_code=404, detail="Listing not found")
        if payload.title is not None:
            lst.title = payload.title
        if payload.description is not None:
            lst.description = payload.description
        if payload.price is not None:
            lst.price = Decimal(str(payload.price))
        if payload.category is not None:
            lst.category_raw = payload.category
        session.commit()
        return {"ok": True, "id": lst.id, "status": lst.status}


@oc_v2_router.post("/listings/{listing_id}/approve")
def oc_approve_listing(listing_id: int, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        approve_listing(session, listing_id)
        session.commit()
        return {"ok": True, "status": ListingStatus.APPROVED.value}


@oc_v2_router.post("/listings/{listing_id}/unapprove")
def oc_unapprove_listing(listing_id: int, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        unapprove_listing(session, listing_id)
        session.commit()
        return {"ok": True, "status": ListingStatus.DRAFT.value}


@oc_v2_router.post("/listings/publish-approved")
def oc_publish_approved(auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        res = publish_approved_listings(session)
        session.commit()
        return res


@oc_v2_router.get("/jobs/{batch_id}")
def oc_get_batch_job(batch_id: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        job = session.get(Job, batch_id)
        if not job:
            stmt = select(Job).where(Job.payload.op("->>")("batch_id") == batch_id)
            job = session.scalars(stmt).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job/Batch not found")
        return {
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "payload": job.payload,
            "result": job.result,
            "error": job.error,
        }


@oc_v2_router.post("/listings/{listing_id}/unpublish")
def oc_unpublish_listing(listing_id: int, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        res = unpublish_listing(session, listing_id)
        session.commit()
        return res


@oc_v2_router.post("/listings/{listing_id}/relist")
def oc_relist_listing(listing_id: int, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        res = relist_listing_now(session, listing_id)
        session.commit()
        return res


# --- MESSAGES ENDPOINTS ---
@oc_v2_router.get("/messages")
def oc_get_messages(unread: bool = False, auth: bool = _AUTH) -> list[dict[str, Any]]:
    with get_db_session() as session:
        return list_threads(session, unread_only=unread)


@oc_v2_router.post("/messages/{thread_id}/suggest")
def oc_suggest_message(thread_id: int, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        suggestion = suggest_reply(session, thread_id)
        return {"suggestion": suggestion}


@oc_v2_router.post("/messages/{thread_id}/send")
def oc_send_message(thread_id: int, payload: SendMessagePayload, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        msg = send_reply(session, thread_id=thread_id, reply_ru=payload.text)
        session.commit()
        return {"ok": True, "message_id": msg.id}


# --- BUY ENDPOINTS ---
@oc_v2_router.post("/buy/search")
def oc_buy_search(payload: BuySearchPayload, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        req, job = create_buy_search(
            session=session,
            query=payload.query,
            currency=payload.currency,
            max_price=Decimal(str(payload.max_price)) if payload.max_price is not None else None,
        )
        session.commit()
        return {"ok": True, "request_id": req.id, "job_id": job.id}


@oc_v2_router.get("/buy/{request_id}")
def oc_get_buy_request(request_id: int, segment: str = "all", auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        req = session.get(BuyRequest, request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Buy request not found")
        candidates = format_candidates(session, request_id, segment=segment)
        return {
            "id": req.id,
            "query": req.query,
            "currency": req.currency,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "candidates": candidates,
        }


@oc_v2_router.post("/buy/{request_id}/kits/analyze")
def oc_analyze_kits(request_id: int, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        candidates = session.scalars(select(BuyCandidate).where(BuyCandidate.request_id == request_id)).all()
        kits = [c for c in candidates if (c.verdict or {}).get("is_kit")]
        results = []
        for k in kits:
            res = analyze_kit(session, k.id)
            results.append({"candidate_id": k.id, "analysis": res})
        session.commit()
        return {"ok": True, "analyzed_count": len(results), "results": results}
