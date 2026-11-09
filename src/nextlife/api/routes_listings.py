"""Routes for listings management section (TZ §5.6, §6).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select

from nextlife.api.routes import check_auth, resolve_photo_url, templates
from nextlife.core.status import ListingStatus
from nextlife.services.listings_reconcile import reconcile_listings_with_marketplaces
from nextlife.services.listings_sort import sort_listings_by_status
from nextlife.services.listings import (
    ListingDeletionForbiddenError, ListingValidationError, approve_listing, delete_listing,
    get_listings_counts, get_marketplace_capabilities, prepare_item_listings,
    publish_approved_listings, publish_listing_now, relist_listing_now, set_listing_free,
    unapprove_listing, unpublish_listing, update_listing,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto, Listing

router = APIRouter(tags=["listings"])


@router.get("/ui/listings", response_class=HTMLResponse)
def screen_listings_list(
    request: Request,
    filter_group: str = Query(default="all", alias="filter"),
    marketplace: str | None = Query(default=None),
    sort: str = Query(default="status"),
    q: str | None = Query(default=None),
    item_code: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render Listings section with chips, search, sort, and direct links (§6.1)."""
    with get_db_session() as session:
        counts = get_listings_counts(session, item_code=item_code)

        stmt = select(Listing).join(Item, Item.code == Listing.item_code).where(Item.status != "DELETED")
        if item_code:
            stmt = stmt.where(Listing.item_code == item_code)

        # Chips filtering (§6.1)
        now = datetime.now(UTC)
        if item_code and filter_group == "all":
            # For a single item, "all" displays all active/draft/approved/published listings
            stmt = stmt.where(Listing.status.notin_([ListingStatus.ARCHIVED.value, "ARCHIVED"]))
        elif filter_group == "all":
            stmt = stmt.where(Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value]))
        elif filter_group == "published":
            stmt = stmt.where(Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value]))
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
                Listing.status.in_([ListingStatus.PUBLISHED.value, ListingStatus.UPDATING.value]),
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
        sort_map = {"first_published_asc": Listing.first_published_at.asc().nulls_last(), "first_published_desc": Listing.first_published_at.desc().nulls_last(), "price": desc(Listing.price)}
        stmt = stmt.order_by(sort_map.get(sort, desc(Listing.created_at)))

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

        if not sort or sort == "status":
            formatted = sort_listings_by_status(formatted)

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
                "item_code": item_code,
                "active_tab": "listings",
            },
        )


@router.get("/ui/listings/{listing_id}", response_class=HTMLResponse)
def screen_listing_detail(
    request: Request,
    listing_id: str,
    notice: str | None = Query(default=None),
    return_to: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render single listing detail/edit screen (§6.2)."""
    with get_db_session() as session:
        listing = session.get(Listing, listing_id)
        if not listing:
            raise HTTPException(status_code=404, detail="Объявление не найдено")

        item = session.get(Item, listing.item_code)
        caps = get_marketplace_capabilities(listing.marketplace_key)

        from nextlife.services.listing_categories import (
            get_listing_category_options,
            get_marketplace_category_choice,
        )

        category_key = listing.category_key or ""
        category_path = listing.category_path or ""
        if not category_key and not category_path:
            category_key, category_path = get_marketplace_category_choice(
                item.category_key if item else "general",
                listing.marketplace_key,
                text=f"{listing.title or ''} {listing.description or ''}",
            )
            listing.category_key = category_key
            listing.category_path = category_path
            session.commit()

        category_groups = get_listing_category_options(listing.marketplace_key)

        photos = session.scalars(select(ItemPhoto).where(ItemPhoto.item_code == listing.item_code)).all()
        photos_data = [{"id": p.id, "url": resolve_photo_url(p.path), "selected": p.id in (listing.photo_ids or [])} for p in photos]

        from nextlife.storage.models import DeliveryOption
        from nextlife.services.marketplace_shipping import filter_compatible_delivery_options
        from nextlife.services.listings import get_marketplace_base_url
        from datetime import date
        from decimal import Decimal
        from nextlife.money.money import convert_amount

        all_delivs = session.scalars(select(DeliveryOption).where(DeliveryOption.item_code == listing.item_code).order_by(DeliveryOption.price.asc())).all()
        comp_delivs_raw, incomp_delivs_raw = filter_compatible_delivery_options(all_delivs, listing.marketplace_key)

        item_currency = (getattr(item, "target_currency", None) or "CZK").strip().upper() if item else "CZK"
        target_currency = (listing.currency or "CZK").strip().upper()

        def _to_deliv(d: Any) -> SimpleNamespace:
            raw_p = getattr(d, "price", Decimal("0"))
            if not isinstance(raw_p, Decimal):
                raw_p = Decimal(str(raw_p or 0))
            conv_p = raw_p
            if target_currency != item_currency and raw_p:
                try:
                    conv_p = convert_amount(session, raw_p, item_currency, target_currency, date.today())
                except Exception:
                    conv_p = raw_p
            price_display = f"{conv_p:.2f}" if (target_currency in ("EUR", "USD") or conv_p % 1 != 0) else str(int(conv_p))
            return SimpleNamespace(
                id=str(getattr(d, "id", "")),
                service_name=str(getattr(d, "service_name", "")),
                carrier_key=str(getattr(d, "carrier_key", "")),
                price=price_display,
                currency=target_currency,
            )

        comp_delivs = [_to_deliv(d) for d in comp_delivs_raw]
        incomp_delivs = [_to_deliv(d) for d in incomp_delivs_raw]

        listing_data = SimpleNamespace(
            id=str(listing.id), item_code=str(listing.item_code), marketplace_key=str(listing.marketplace_key),
            external_id=listing.external_id,
            external_url=listing.external_url or (get_marketplace_base_url(listing.marketplace_key) if listing.status in ("PUBLISHED", "UPDATING") else None),
            price=str(listing.price) if listing.price is not None else "0",
            currency=str(listing.currency or "CZK"), status=str(listing.status or "DRAFT"),
            title=listing.title or "", description=listing.description or "",
            category_path=category_path, category_key=category_key, category_auto=bool(listing.category_auto),
            lang=listing.lang or "cs", photo_ids=list(listing.photo_ids or []),
            first_published_at=listing.first_published_at, expires_at=listing.expires_at,
            auto_relist=bool(getattr(listing, "auto_relist", False)),
        )
        item_data = SimpleNamespace(code=item.code, title=item.title or "") if item else None

    return templates.TemplateResponse(
        request=request, name="listing_detail.html",
        context={
            "listing": listing_data, "item": item_data, "caps": caps, "photos": photos_data,
            "category_groups": category_groups,
            "compatible_deliveries": comp_delivs, "incompatible_deliveries": incomp_delivs,
            "return_to": return_to or (f"/ui/stock/{listing_data.item_code}#listings" if listing_data else "/ui/listings"),
            "notice": notice, "active_tab": "listings",
        },
    )


@router.post("/ui/listings/{listing_id}/save")
def ui_save_listing(
    listing_id: str,
    title: str = Form(""),
    description: str = Form(""),
    category_key: str = Form(""),
    price: str = Form("0"),
    auto_relist: str | None = Form(default=None),
    return_to: str | None = Form(default=None),
    submit_action: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        listing = update_listing(session, listing_id, {
            "title": title, "description": description,
            "category_key": category_key, "price": price,
            "auto_relist": auto_relist in ("true", "on", "1", "True"),
        })
        item_code = listing.item_code
        session.commit()
    if submit_action == "save_and_back":
        back_url = return_to or f"/ui/stock/{item_code}#listings?notice=saved"
        return RedirectResponse(url=back_url, status_code=status.HTTP_303_SEE_OTHER)
    ret_param = f"&return_to={quote(return_to)}" if return_to else ""
    return RedirectResponse(
        url=f"/ui/listings/{listing_id}?notice=saved{ret_param}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/approve")
def ui_approve_listing(
    listing_id: str,
    return_to: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        try:
            approve_listing(session, listing_id)
            session.commit()
        except ListingValidationError as exc:
            ret = f"&return_to={quote(return_to)}" if return_to else ""
            return RedirectResponse(
                url=f"/ui/listings/{listing_id}?notice=err&reason={quote(str(exc))}{ret}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    dest = return_to or f"/ui/listings/{listing_id}?notice=approved"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/ui/listings/{listing_id}/unapprove")
def ui_unapprove_listing(
    listing_id: str,
    return_to: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        unapprove_listing(session, listing_id)
        session.commit()
    dest = return_to or f"/ui/listings/{listing_id}?notice=unapproved"
    return RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)


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


@router.post("/ui/listings/{listing_id}/publish")
def ui_publish_listing(
    listing_id: str,
    return_to: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        _ = publish_listing_now(session, listing_id)
        session.commit()
    target = return_to or f"/ui/listings/{listing_id}?notice=publish_queued"
    return RedirectResponse(
        url=target,
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/unpublish")
def ui_unpublish_listing(
    listing_id: str,
    return_to: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        res = unpublish_listing(session, listing_id)
        session.commit()
        if not res.get("supported"):
            target_url = return_to or f"/ui/listings/{listing_id}"
            sep = "&" if "?" in target_url else "?"
            return RedirectResponse(
                url=f"{target_url}{sep}notice=unsupported&reason={quote(str(res.get('message') or ''))}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    target = return_to or "/ui/listings?notice=unpublished"
    return RedirectResponse(
        url=target,
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/relist")
def ui_relist_listing(
    listing_id: str,
    return_to: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        res = relist_listing_now(session, listing_id)
        session.commit()
        if not res.get("supported"):
            target_url = return_to or f"/ui/listings/{listing_id}"
            sep = "&" if "?" in target_url else "?"
            return RedirectResponse(
                url=f"{target_url}{sep}notice=unsupported&reason={quote(str(res.get('message') or ''))}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    target = return_to or f"/ui/listings/{listing_id}?notice=relisting_queued"
    return RedirectResponse(
        url=target,
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/ui/listings/{listing_id}/delete")
def ui_delete_listing(
    listing_id: str,
    return_to: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    try:
        with get_db_session() as session:
            listing = session.get(Listing, listing_id)
            item_code = listing.item_code if listing else None
            delete_listing(session, listing_id)
            session.commit()
        target_url = return_to or (f"/ui/stock/{item_code}#listings?notice=listing_deleted" if item_code else "/ui/listings?notice=deleted")
        return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    except ListingDeletionForbiddenError as exc:
        sep = "&" if "?" in (return_to or "") else "?"
        reason_enc = quote(str(exc))
        target_url = f"{return_to}{sep}notice=delete_forbidden&reason={reason_enc}" if return_to else f"/ui/listings/{listing_id}?notice=delete_forbidden&reason={reason_enc}"
        return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/ui/listings/recheck-live")
def ui_recheck_live(
    request: Request,
    item_code: str | None = Form(default=None),
    return_to: str | None = Form(default=None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        stats = reconcile_listings_with_marketplaces(session, item_code=item_code)
        session.commit()
    sep = "&" if "?" in (return_to or "") else "?"
    target = f"{return_to}{sep}notice=reconciled" if return_to else (f"/ui/listings?item_code={item_code}&notice=reconciled" if item_code else "/ui/listings?notice=reconciled")
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


# --- Preparation, Recheck and Batch Publish endpoints ---


@router.post("/api/stock/{code}/listings/prepare")
@router.post("/ui/stock/{code}/listings/prepare")
def api_prepare_listings(code: str, request: Request, auth: bool = Depends(check_auth)) -> Any:
    """Prepare draft listings for item (§5.6)."""
    with get_db_session() as session:
        created = prepare_item_listings(session, code)
        session.commit()
    if request.url.path.startswith("/ui/"):
        return RedirectResponse(url=f"/ui/stock/{code}#listings", status_code=status.HTTP_303_SEE_OTHER)
    return {"status": "ok", "item_code": code, "prepared_count": len(created), "listings": [row.id for row in created]}


@router.post("/api/listings/publish-approved")
@router.post("/ui/listings/publish-approved")
def api_publish_approved(request: Request, item_code: str | None = Form(default=None), auth: bool = Depends(check_auth)) -> Any:
    """Create batch of publish jobs for approved listings (§6.3)."""
    with get_db_session() as session:
        try:
            batch_id = publish_approved_listings(session, item_code=item_code)
            session.commit()
        except ListingValidationError as exc:
            if request.url.path.startswith("/ui/"):
                return RedirectResponse(url=f"/ui/listings?notice=err&reason={exc}", status_code=status.HTTP_303_SEE_OTHER)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.url.path.startswith("/ui/"):
        return RedirectResponse(url=f"/ui/jobs/batches/{batch_id}", status_code=status.HTTP_303_SEE_OTHER)
    return {"status": "ok", "batch_id": batch_id}
