"""ТЗ «Склад» вместо «Ленты», этап 3 — маршруты раздела «Склад».

GET /ui/stock, GET /ui/stock/new, GET /ui/stock/{code} — экраны.
POST /api/stock, PATCH /api/stock/{code}, POST /api/stock/{code}/sold,
GET /api/stock/{code}/delete-preview, DELETE /api/stock/{code} — Web API форм.
Все операции вызывают только сервис nextlife.services.stock (§1.4).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from nextlife.api.routes import (
    check_auth,
    get_status_badge_class,
    logical_photo_entries,
    templates,
)
from nextlife.core.categories import load_category_options
from nextlife.core.shipping_policy import shipping_policy_options
from nextlife.services.lot_delete import LotDeletionBlockedError
from nextlife.services.shipping_options import get_lot_delivery_summary
from nextlife.services.stock import (
    StockDeletionNeedsConfirmationError,
    StockItemNotFoundError,
    StockValidationError,
    bulk_update_location,
    create_stock_item,
    delete_preview,
    delete_stock_item,
    dispose_item,
    get_stock_group_counts,
    get_stock_item,
    list_stock,
    mark_sold,
    return_item_warranty,
    save_price_estimate,
    set_item_ready_for_sale,
    update_stock_item,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item

stock_router = APIRouter(tags=["stock"])

# Числовые поля доставки (§3.3 группа 4).
_INT_FIELDS = ("weight_g", "packed_weight_g", "dim_l_mm", "dim_w_mm", "dim_h_mm")


def _clean(val: str | None) -> str | None:
    if val is None:
        return None
    val = val.strip()
    return val if val != "" else None


def _clean_int(val: str | None) -> int | None:
    v = _clean(val)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _listing_row_status(status: str) -> str:
    """Отображение статуса объявления (§3.3)."""
    if status == "PUBLISHED":
        return "Размещено"
    if status == "UPDATING":
        return "Обновляется"
    if status == "ARCHIVED":
        return "Снято"
    if status == "UNPUBLISH_FAILED":
        return "Не снято — снимите вручную"
    return status


def _item_screen_context(request: Request, code: str | None, notice: str | None, **extra: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request": request,
        "active_tab": "items",
        "notice": notice,
        "listing_row_status": _listing_row_status,
        "shipping_policy_options": shipping_policy_options(),
        "category_options": load_category_options(),
    }
    if code:
        with get_db_session() as session:
            item = get_stock_item(session, code)
            item["status_badge_cls"] = get_status_badge_class(item["status"])
            from nextlife.core.config import load_config
            from nextlife.services.category_gate import rejections_for_item
            from nextlife.services.facebook_pack import clipboard_text

            item["category_rejections"] = rejections_for_item(load_config(), item.get("category_key"))
            item["facebook_text"] = clipboard_text(
                title=item.get("title") or "",
                price=str(item.get("target_price") or ""),
                currency=item.get("target_currency") or "",
                description=item.get("description") or "",
            )
            entries = logical_photo_entries(session, code)
            item["photos"] = entries
            # _photo_gallery.html обращается к верхнеуровневому `photos`.
            extra["photos"] = entries
            item["thumb_url"] = entries[0]["url"] if entries and not entries[0]["broken"] else None
            # Конструктор перевозчиков (§3.3): тот же сервис shipping_options.
            try:
                import json as _json

                summary = get_lot_delivery_summary(session, code)
                item["shipping_summary"] = summary
                item["shipping_summary_json"] = _json.dumps(summary, ensure_ascii=False)
            except ValueError:
                item["shipping_summary"] = None
                item["shipping_summary_json"] = "{}"

            # Данные разведки аналогов для Этапа 1 (Оценка цены)
            try:
                from nextlife.services.research import build_research_panel

                item_model = session.get(Item, code)
                if item_model:
                    ctx["research"] = build_research_panel(session, item_model, load_config())
            except Exception:
                ctx["research"] = None
        ctx["item"] = item
    ctx.update(extra)
    return ctx


# --- Экраны -----------------------------------------------------------------


@stock_router.get("/ui/stock", response_class=HTMLResponse)
def screen_stock_list(
    request: Request,
    group: str = Query(default="all"),
    sort: str = Query(default="recent"),
    q: str | None = Query(default=None),
    notice: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    with get_db_session() as session:
        try:
            rows = list_stock(session, status_group=group, q=q, sort=sort)
        except StockValidationError:
            rows = list_stock(session, status_group="all", q=q, sort=sort)
            group = "all"
        group_counts = get_stock_group_counts(session)
        for r in rows:
            r["status_badge_cls"] = get_status_badge_class(r["status"])
            entries = logical_photo_entries(session, r["code"])
            r["thumb_url"] = entries[0]["url"] if entries and not entries[0]["broken"] else None
    return templates.TemplateResponse(
        request=request,
        name="stock.html",
        context={
            "items": rows,
            "group": group,
            "sort": sort,
            "q": q or "",
            "group_counts": group_counts,
            "active_tab": "stock",
            "notice": notice,
        },
    )


@stock_router.get("/ui/stock/new", response_class=HTMLResponse)
def screen_stock_new(
    request: Request,
    location: str | None = Query(default=None),
    notice: str | None = Query(default=None),
    created_code: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="stock_new.html",
        context={
            "location": location or "",
            "notice": notice,
            "created_code": created_code,
            "category_options": load_category_options(),
            "shipping_policy_options": shipping_policy_options(),
            "active_tab": "stock",
        },
    )


@stock_router.get("/ui/photo-stand", response_class=HTMLResponse)
def screen_photo_stand(
    request: Request,
    codes: str = Query(default=""),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Режим «Фотостенд» для последовательной съёмки предметов (§5.3)."""
    raw_codes = [c.strip() for c in codes.split(",") if c.strip()]
    if not raw_codes:
        return RedirectResponse(url="/ui/stock", status_code=status.HTTP_303_SEE_OTHER)

    items_data = []
    with get_db_session() as session:
        for code in raw_codes:
            item = session.get(Item, code)
            if not item:
                continue
            photos = logical_photo_entries(session, code)
            attrs = item.attrs or {}
            samples = []
            selected_analogs = attrs.get("wizard_selected") or []
            if isinstance(selected_analogs, list):
                for a in selected_analogs:
                    if isinstance(a, dict) and a.get("photo_url"):
                        samples.append({"url": a.get("photo_url"), "title": a.get("title", "")})
            items_data.append({
                "code": item.code,
                "title": item.title,
                "storage_location": item.storage_location,
                "photos": photos,
                "photos_count": len(photos),
                "samples": samples,
            })

    if not items_data:
        return RedirectResponse(url="/ui/stock", status_code=status.HTTP_303_SEE_OTHER)

    import json as _json

    return templates.TemplateResponse(
        request=request,
        name="photo_stand.html",
        context={
            "items": items_data,
            "items_json": _json.dumps(items_data, ensure_ascii=False),
            "codes_param": codes,
        },
    )


@stock_router.get("/ui/stock/{code}", response_class=HTMLResponse)
def screen_stock_item(
    request: Request,
    code: str,
    notice: str | None = Query(default=None),
    sold_results: str | None = Query(default=None),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    try:
        ctx = _item_screen_context(request, code, notice)
    except StockItemNotFoundError:
        raise HTTPException(status_code=404, detail=f"Предмет #{code} не найден") from None
    # Результат снятия по площадкам после «Продан» (§3.3): "mp:archived;mp2:failed".
    if sold_results:
        rows = []
        for part in sold_results.split(";"):
            if ":" in part:
                mp, res = part.split(":", 1)
                rows.append({"marketplace": mp, "result": res})
        ctx["sold_rows"] = rows
    return templates.TemplateResponse(
        request=request,
        name="stock_item.html",
        context=ctx,
    )


# --- Web API форм -----------------------------------------------------------


@stock_router.post("/api/stock")
def api_stock_create(
    request: Request,
    title: str = Form(""),
    action: str = Form("create"),
    category_key: str | None = Form(None),
    storage_location: str | None = Form(None),
    description_lang: str | None = Form(None),
    target_price: str | None = Form(None),
    target_currency: str | None = Form(None),
    description: str | None = Form(None),
    usage: str | None = Form(None),
    specs: str | None = Form(None),
    notes: str | None = Form(None),
    shipping_policy: str | None = Form(None),
    weight_g: str | None = Form(None),
    packed_weight_g: str | None = Form(None),
    dim_l_mm: str | None = Form(None),
    dim_w_mm: str | None = Form(None),
    dim_h_mm: str | None = Form(None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    from urllib.parse import quote

    from nextlife.services.listing_categories import infer_item_category
    from nextlife.services.stock import StockValidationError as _SVE

    cat = _clean(category_key)
    if not cat or cat == "general":
        inferred = infer_item_category(title, description or "")
        if inferred != "general":
            cat = inferred

    fields: dict[str, Any] = {
        "title": _clean(title) or "",
        "category_key": cat or "general",
        "description_lang": _clean(description_lang),
        "price": _clean(target_price),
        "currency": _clean(target_currency),
        "storage_location": _clean(storage_location),
        "description": _clean(description),
        "usage": _clean(usage),
        "specs": _clean(specs),
        "notes": _clean(notes),
        "shipping_policy": _clean(shipping_policy) or "BUYER_PAYS",
        "weight_g": _clean_int(weight_g),
        "packed_weight_g": _clean_int(packed_weight_g),
        "dim_l_mm": _clean_int(dim_l_mm),
        "dim_w_mm": _clean_int(dim_w_mm),
        "dim_h_mm": _clean_int(dim_h_mm),
    }
    with get_db_session() as session:
        try:
            result = create_stock_item(session, fields)
            session.commit()
            code = result["code"]
        except _SVE as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if action == "create_and_next":
        loc_param = f"&location={quote(storage_location or '')}" if storage_location else ""
        return RedirectResponse(
            url=f"/ui/stock/new?notice=created_next&created_code={code}{loc_param}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(url=f"/ui/stock/{code}#price", status_code=status.HTTP_303_SEE_OTHER)


class BulkLocationRequest(BaseModel):
    codes: list[str]
    storage_location: str


@stock_router.patch("/api/stock/bulk-location")
def api_stock_bulk_location(
    data: BulkLocationRequest,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    with get_db_session() as session:
        count = bulk_update_location(session, data.codes, data.storage_location)
        session.commit()
        return {"status": "ok", "updated_count": count}


class BulkCodesRequest(BaseModel):
    codes: list[str]


@stock_router.post("/api/stock/bulk-price-estimate")
def api_stock_bulk_price_estimate(
    data: BulkCodesRequest,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    with get_db_session() as session:
        from nextlife.jobs.manager import create_job
        job_ids = []
        for code in data.codes:
            j = create_job(session, kind="PRICE_RESEARCH", payload={"item_code": code})
            job_ids.append(j.id)
        session.commit()
        return {"status": "ok", "job_ids": job_ids, "queued_count": len(job_ids)}


@stock_router.api_route("/api/stock/{code}", methods=["POST", "PATCH"])
def api_stock_update(
    request: Request,
    code: str,
    title: str | None = Form(None),
    category_key: str | None = Form(None),
    description_lang: str | None = Form(None),
    target_price: str | None = Form(None),
    target_currency: str | None = Form(None),
    price_eur: str | None = Form(None),
    storage_location: str | None = Form(None),
    description: str | None = Form(None),
    usage: str | None = Form(None),
    specs: str | None = Form(None),
    notes: str | None = Form(None),
    shipping_policy: str | None = Form(None),
    weight_g: str | None = Form(None),
    packed_weight_g: str | None = Form(None),
    dim_l_mm: str | None = Form(None),
    dim_w_mm: str | None = Form(None),
    dim_h_mm: str | None = Form(None),
    warranty_months: str | None = Form(None),
    warranty_terms: str | None = Form(None),
    next: str | None = Form(None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    fields: dict[str, Any] = {
        "title": _clean(title),
        "category_key": _clean(category_key),
        "description_lang": _clean(description_lang),
        "target_price": _clean(target_price),
        "target_currency": _clean(target_currency),
        "price_eur": _clean(price_eur),
        "storage_location": _clean(storage_location),
        "description": _clean(description),
        "usage": _clean(usage),
        "specs": _clean(specs),
        "notes": _clean(notes),
        "shipping_policy": _clean(shipping_policy),
        "weight_g": _clean_int(weight_g),
        "packed_weight_g": _clean_int(packed_weight_g),
        "dim_l_mm": _clean_int(dim_l_mm),
        "dim_w_mm": _clean_int(dim_w_mm),
        "dim_h_mm": _clean_int(dim_h_mm),
        "warranty_months": _clean_int(warranty_months),
        "warranty_terms": _clean(warranty_terms),
    }
    with get_db_session() as session:
        try:
            result = update_stock_item(session, code, fields)
            session.commit()
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StockValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        not_updated = result.get("listings_not_updated", False)
    notice = "saved_not_updated" if not_updated else "saved"
    # Optional return URL (wizard step 2 passes next=/ui/lot-wizard/text?item_code=…).
    if next:
        sep = "&" if "?" in next else "?"
        return RedirectResponse(url=f"{next}{sep}notice={notice}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/ui/stock/{code}?notice={notice}#describe", status_code=status.HTTP_303_SEE_OTHER
    )


@stock_router.post("/api/stock/{code}/ready")
def api_stock_ready(
    code: str,
    auth: bool = Depends(check_auth),
) -> Any:
    """Validate checklist and mark item READY_FOR_SALE (ТЗ §5.5)."""
    with get_db_session() as session:
        ready, missing = set_item_ready_for_sale(session, code)
        if not ready:
            raise HTTPException(
                status_code=409,
                detail={"error": "Чек-лист не выполнен", "missing": missing},
            )
        session.commit()
    return RedirectResponse(url=f"/ui/stock/{code}?notice=ready#describe", status_code=status.HTTP_303_SEE_OTHER)


@stock_router.post("/api/stock/{code}/dispose")
def api_stock_dispose(
    code: str,
    reason: str = Form("Не продано / нерентабельно"),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Dispose unviable item (§5.2): status DISPOSED, archive stock."""
    with get_db_session() as session:
        dispose_item(session, code, reason=reason)
        session.commit()
    return RedirectResponse(url="/ui/stock?group=archive&notice=disposed", status_code=status.HTTP_303_SEE_OTHER)


@stock_router.post("/api/stock/{code}/return")
def api_stock_return(
    code: str,
    reason: str = Form("Возврат по гарантии"),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Mark item RETURNED under warranty (§5.7)."""
    with get_db_session() as session:
        return_item_warranty(session, code, reason=reason)
        session.commit()
    return RedirectResponse(url=f"/ui/stock/{code}?notice=returned#warranty", status_code=status.HTTP_303_SEE_OTHER)


class PriceEstimateRequest(BaseModel):
    price: str
    currency: str = "CZK"
    median: str | None = None
    low: str | None = None
    high: str | None = None


@stock_router.post("/api/stock/{code}/price-estimate")
def api_stock_price_estimate(
    code: str,
    price: str = Form(...),
    currency: str = Form("CZK"),
    price_eur: str | None = Form(None),
    median: str | None = Form(None),
    low: str | None = Form(None),
    high: str | None = Form(None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Save price estimate decision and move item to ESTIMATED (§5.2)."""
    with get_db_session() as session:
        save_price_estimate(
            session=session,
            code=code,
            target_price=price,
            currency=currency,
            price_eur=price_eur,
            median=median,
            low=low,
            high=high,
        )
        session.commit()
    return RedirectResponse(url=f"/ui/stock/{code}?notice=price_estimated#price", status_code=status.HTTP_303_SEE_OTHER)


@stock_router.post("/api/stock/{code}/sold")
def api_stock_sold(
    request: Request,
    code: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        try:
            result = mark_sold(session, code, actor="OWNER")
            session.commit()
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Компактная сводка снятия по площадкам для экрана: "mp:archived;mp2:failed".
    parts = [f"{r['marketplace_key']}:{r['result']}" for r in result.get("unpublish", [])]
    sold_results = ";".join(parts)
    sold_param = ""
    if sold_results:
        from urllib.parse import quote

        sold_param = f"&sold_results={quote(sold_results)}"
    return RedirectResponse(url=f"/ui/stock/{code}?notice=sold{sold_param}#sale", status_code=status.HTTP_303_SEE_OTHER)


@stock_router.get("/api/stock/{code}/delete-preview")
def api_stock_delete_preview(
    code: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    with get_db_session() as session:
        try:
            return delete_preview(session, code)
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@stock_router.delete("/api/stock/{code}")
def api_stock_delete(
    code: str,
    confirm_listings: bool = Query(default=False),
    confirm_deals: bool = Query(default=False),
    auth: bool = Depends(check_auth),
) -> JSONResponse:
    with get_db_session() as session:
        try:
            result = delete_stock_item(
                session, code, confirm_listings=confirm_listings, confirm_deals=confirm_deals
            )
            session.commit()
            return JSONResponse({"status": "DELETED", **result})
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StockDeletionNeedsConfirmationError as exc:
            preview = delete_preview(session, code)
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": str(exc), "delete_preview": preview},
            )
        except LotDeletionBlockedError as exc:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": str(exc)},
            )
