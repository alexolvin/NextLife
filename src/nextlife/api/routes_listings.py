"""Routes for listings management section (TZ §5.6, §6).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select

from nextlife.api.routes import check_auth, resolve_photo_url, templates
from nextlife.core.status import ListingStatus
from nextlife.services.listings import (
    ListingValidationError,
    approve_listing,
    delete_listing,
    get_listings_counts,
    get_marketplace_capabilities,
    prepare_item_listings,
    publish_approved_listings,
    relist_listing_now,
    set_listing_free,
    unapprove_listing,
    unpublish_listing,
    update_listing,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto, Listing

router = APIRouter(tags=["listings"])


@router.get("/ui/listings", response_class=HTMLResponse)
def screen_listings_list(
    request: Request,
    filter_group: str = Query(default="all", alias="filter"),
    marketplace: str | None = Query(default=None),
    sort: str = Query(default="first_published_asc"),
    q: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render Listings section with chips, search, sort, and direct links (§6.1)."""
    with get_db_session() as session:
        counts = get_listings_counts(session)

        stmt = select(Listing)

        # Chips filtering (§6.1)
        now = datetime.now(UTC)
        if filter_group == "all":
            stmt = stmt.where(Listing.status.in_([ListingStatus.PUBLISHED.value, "ACTIVE"]))
        elif filter_group == "errors":
            stmt = stmt.where(
                Listing.status.in_([ListingStatus.FAILED.value, ListingStatus.UNPUBLISH_FAILED.value])
            )
        elif filter_group == "approved":
            stmt = stmt.where(Listing.status == ListingStatus.APPROVED.value)
        elif filter_group == "drafts":
            stmt = stmt.where(Listing.status == ListingStatus.DRAFT.value)
        elif filter_group == "expiring":
            three_days = now + timedelta(days=3)
            stmt = stmt.where(
                Listing.status.in_([ListingStatus.PUBLISHED.value, "ACTIVE"]),
                Listing.expires_at.is_not(None),
                Listing.expires_at <= three_days,
            )
        elif filter_group == "archived":
            stmt = stmt.where(Listing.status.in_([ListingStatus.ARCHIVED.value, "ARCHIVED"]))

        if marketplace:
            stmt = stmt.where(Listing.marketplace_key == marketplace)

        if q and q.strip():
            query_str = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                Listing.title.ilike(query_str) | Listing.item_code.ilike(query_str)
            )

        # Sorting (§6.1)
        if sort == "first_published_asc":
            stmt = stmt.order_by(Listing.first_published_at.asc().nulls_last())
        elif sort == "first_published_desc":
            stmt = stmt.order_by(Listing.first_published_at.desc().nulls_last())
        elif sort == "price":
            stmt = stmt.order_by(desc(Listing.price))
        else:
            stmt = stmt.order_by(desc(Listing.created_at))

        listings = session.scalars(stmt).all()

        formatted = []
        for row in listings:
            item = session.get(Item, row.item_code)
            # Find cover thumb
            thumb_url = None
            if row.photo_ids:
                p = session.get(ItemPhoto, row.photo_ids[0])
                if p:
                    thumb_url = resolve_photo_url(p.path)

            days_ago = None
            if row.first_published_at:
                days_ago = max(0, (now - row.first_published_at).days)

            formatted.append({
                "id": row.id,
                "item_code": row.item_code,
                "item_title": item.title if item else row.item_code,
                "title": row.title or (item.title if item else row.item_code),
                "marketplace_key": row.marketplace_key,
                "price": str(row.price),
                "currency": row.currency,
                "status": row.status,
                "thumb_url": thumb_url,
                "days_ago": days_ago,
                "external_url": row.external_url,
                "last_error_text": row.last_error_text,
                "last_error_hint": row.last_error_hint,
                "first_published_at": row.first_published_at,
                "expires_at": row.expires_at,
                "messages_count": 0,
            })

    return templates.TemplateResponse(
        request=request,
        name="listings.html",
        context={
            "listings": formatted,
            "filter_group": filter_group,
            "marketplace_filter": marketplace,
            "sort": sort,
            "q": q or "",
            "counts": counts,
            "active_tab": "listings",
        },
    )


@router.get("/ui/listings/{listing_id}", response_class=HTMLResponse)
def screen_listing_detail(
    request: Request,
    listing_id: str,
    notice: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render single listing detail/edit screen (§6.2)."""
    with get_db_session() as session:
        listing = session.get(Listing, listing_id)
        if not listing:
            raise HTTPException(status_code=404, detail="Объявление не найдено")

        item = session.get(Item, listing.item_code)
        caps = get_marketplace_capabilities(listing.marketplace_key)

        # Photos of item
        photos = session.scalars(
            select(ItemPhoto).where(ItemPhoto.item_code == listing.item_code)
        ).all()
        photos_data = [
            {
                "id": p.id,
                "url": resolve_photo_url(p.path),
                "selected": p.id in (listing.photo_ids or []),
            }
            for p in photos
        ]

    return templates.TemplateResponse(
        request=request,
        name="listing_detail.html",
        context={
            "listing": listing,
            "item": item,
            "caps": caps,
            "photos": photos_data,
            "notice": notice,
            "active_tab": "listings",
        },
    )


@router.post("/ui/listings/{listing_id}/save")
def ui_save_listing(
    listing_id: str,
    title: str = Form(""),
    description: str = Form(""),
    category_key: str = Form(""),
    price: str = Form("0"),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        update_listing(session, listing_id, {
            "title": title,
            "description": description,
            "category_key": category_key,
            "price": price,
        })
        session.commit()
    return RedirectResponse(
        url=f"/ui/listings/{listing_id}?notice=saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/approve")
def ui_approve_listing(
    listing_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        try:
            approve_listing(session, listing_id)
            session.commit()
        except ListingValidationError as exc:
            return RedirectResponse(
                url=f"/ui/listings/{listing_id}?notice=err&reason={exc}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    return RedirectResponse(
        url=f"/ui/listings/{listing_id}?notice=approved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/unapprove")
def ui_unapprove_listing(
    listing_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        unapprove_listing(session, listing_id)
        session.commit()
    return RedirectResponse(
        url=f"/ui/listings/{listing_id}?notice=unapproved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/free")
def ui_set_listing_free(
    listing_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        set_listing_free(session, listing_id, is_free=True)
        session.commit()
    return RedirectResponse(
        url=f"/ui/listings/{listing_id}?notice=free_enabled",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/unpublish")
def ui_unpublish_listing(
    listing_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        res = unpublish_listing(session, listing_id)
        session.commit()
        if not res.get("supported"):
            return RedirectResponse(
                url=f"/ui/listings/{listing_id}?notice=unsupported&reason={res.get('message')}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    return RedirectResponse(
        url="/ui/listings?notice=unpublished",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/relist")
def ui_relist_listing(
    listing_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        res = relist_listing_now(session, listing_id)
        session.commit()
        if not res.get("supported"):
            return RedirectResponse(
                url=f"/ui/listings/{listing_id}?notice=unsupported&reason={res.get('message')}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    return RedirectResponse(
        url=f"/ui/listings/{listing_id}?notice=relisting_queued",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/delete")
def ui_delete_listing(
    listing_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        delete_listing(session, listing_id)
        session.commit()
    return RedirectResponse(
        url="/ui/listings?notice=deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# --- Preparation and Batch Publish endpoints ---


@router.post("/api/stock/{code}/listings/prepare")
@router.post("/ui/stock/{code}/listings/prepare")
def api_prepare_listings(
    code: str,
    request: Request,
    auth: bool = Depends(check_auth),
) -> Any:
    """Prepare draft listings for item (§5.6)."""
    with get_db_session() as session:
        created = prepare_item_listings(session, code)
        session.commit()
        if request.url.path.startswith("/ui/"):
            return RedirectResponse(
                url=f"/ui/stock/{code}#listings",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return {
            "status": "ok",
            "item_code": code,
            "prepared_count": len(created),
            "listings": [row.id for row in created],
        }


@router.post("/api/listings/publish-approved")
@router.post("/ui/listings/publish-approved")
def api_publish_approved(
    request: Request,
    item_code: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> Any:
    """Create batch of publish jobs for approved listings (§6.3)."""
    with get_db_session() as session:
        try:
            batch_id = publish_approved_listings(session, item_code=item_code)
            session.commit()
        except ListingValidationError as exc:
            if request.url.path.startswith("/ui/"):
                return RedirectResponse(
                    url=f"/ui/listings?notice=err&reason={exc}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if request.url.path.startswith("/ui/"):
        return RedirectResponse(
            url=f"/ui/jobs/{batch_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return {"status": "ok", "batch_id": batch_id}
