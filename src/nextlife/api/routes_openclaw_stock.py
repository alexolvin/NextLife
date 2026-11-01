"""OpenClaw-skill HTTP API — Склад (Warehouse) gateway (ТЗ «Склад» этап 5, §5.1).

Thin backend for the ``nextlife stock ...`` CLI. Every endpoint is
authenticated ONLY with NEXTLIFE_OPENCLAW_TOKEN (``check_openclaw_auth``);
no web-token fallback. All operations call only the ``nextlife.services.stock``
module (§1.4) — no direct writes of Склад fields from routes.

Request/response field names match the §1.1 columns. Carriers are passed as
``delivery_options: [{carrier_key, price, is_enabled}]`` and persisted via the
``shipping_options`` service.

Error codes: 404 — предмет не найден; 409 — нужно решение владельца / операция
заблокирована; 422 — ошибка полей.

Module length rule VI.1.1 (<= 400 lines).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from nextlife.api.routes import logical_photo_entries
from nextlife.api.routes_openclaw import check_openclaw_auth
from nextlife.services.lot_delete import LotDeletionBlockedError
from nextlife.services.shipping_options import configure_lot_delivery_options
from nextlife.services.stock import (
    StockDeletionNeedsConfirmationError,
    StockItemNotFoundError,
    StockValidationError,
    create_stock_item,
    delete_preview,
    delete_stock_item,
    get_stock_item,
    list_stock,
    mark_sold,
    update_stock_item,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import ItemPhoto

oc_stock_router = APIRouter(prefix="/api/oc/stock", tags=["openclaw"])
_AUTH = Depends(check_openclaw_auth)


class OcStockCreate(BaseModel):
    title: str
    description: str | None = None
    description_lang: str | None = None
    usage: str | None = None
    specs: str | None = None
    notes: str | None = None
    storage_location: str | None = None
    target_price: float | None = None
    target_currency: str | None = None
    weight_g: int | None = None
    packed_weight_g: int | None = None
    dim_l_mm: int | None = None
    dim_w_mm: int | None = None
    dim_h_mm: int | None = None
    shipping_policy: str | None = None
    delivery_options: list[dict[str, Any]] | None = None


class OcStockPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    description_lang: str | None = None
    usage: str | None = None
    specs: str | None = None
    notes: str | None = None
    storage_location: str | None = None
    target_price: float | None = None
    target_currency: str | None = None
    weight_g: int | None = None
    packed_weight_g: int | None = None
    dim_l_mm: int | None = None
    dim_w_mm: int | None = None
    dim_h_mm: int | None = None
    shipping_policy: str | None = None
    delivery_options: list[dict[str, Any]] | None = None


# Item fields accepted by update_stock_item (column names, §1.1).
_ITEM_FIELDS = (
    "title",
    "description",
    "description_lang",
    "usage",
    "specs",
    "notes",
    "storage_location",
    "target_price",
    "target_currency",
    "weight_g",
    "packed_weight_g",
    "dim_l_mm",
    "dim_w_mm",
    "dim_h_mm",
    "shipping_policy",
)


def _chain_photo_ids(session: Any, code: str, photo_id: str) -> list[str]:
    """IDs of a photo chain (root + all derived) for the delete response."""
    photo = session.get(ItemPhoto, photo_id)
    if not photo or photo.item_code != code:
        return []
    ids = [photo_id]
    seen = {photo_id}
    frontier = [photo_id]
    while frontier:
        children = session.scalars(
            select(ItemPhoto).where(ItemPhoto.derived_from_id.in_(frontier))
        ).all()
        children = [c for c in children if c.id not in seen]
        if not children:
            break
        seen.update(c.id for c in children)
        ids.extend(c.id for c in children)
        frontier = [c.id for c in children]
    return ids


@oc_stock_router.get("")
def oc_stock_list(
    group: str = Query(default="all"),
    q: str | None = Query(default=None),
    auth: bool = _AUTH,
) -> dict[str, Any]:
    with get_db_session() as session:
        try:
            rows = list_stock(session, group, q)
        except StockValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"items": rows, "count": len(rows)}


@oc_stock_router.post("", status_code=status.HTTP_201_CREATED)
def oc_stock_create(body: OcStockCreate, auth: bool = _AUTH) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "title": body.title,
        "price": body.target_price,
        "currency": body.target_currency,
        "description": body.description,
        "description_lang": body.description_lang,
        "usage": body.usage,
        "specs": body.specs,
        "notes": body.notes,
        "storage_location": body.storage_location,
        "weight_g": body.weight_g,
        "packed_weight_g": body.packed_weight_g,
        "dim_l_mm": body.dim_l_mm,
        "dim_w_mm": body.dim_w_mm,
        "dim_h_mm": body.dim_h_mm,
        "shipping_policy": body.shipping_policy or "BUYER_PAYS",
    }
    with get_db_session() as session:
        try:
            result = create_stock_item(session, fields)
            code = result["code"]
            if body.delivery_options is not None:
                configure_lot_delivery_options(
                    session, code, body.delivery_options, shipping_policy=body.shipping_policy
                )
            session.commit()
            return get_stock_item(session, code)
        except StockValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@oc_stock_router.get("/{code}")
def oc_stock_show(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        try:
            return get_stock_item(session, code)
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@oc_stock_router.patch("/{code}")
def oc_stock_patch(code: str, body: OcStockPatch, auth: bool = _AUTH) -> dict[str, Any]:
    fields = {f: getattr(body, f) for f in _ITEM_FIELDS if getattr(body, f) is not None}
    with get_db_session() as session:
        try:
            result = update_stock_item(session, code, fields)
            if body.delivery_options is not None:
                configure_lot_delivery_options(
                    session, code, body.delivery_options, shipping_policy=body.shipping_policy
                )
            session.commit()
            return result
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StockValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@oc_stock_router.post("/{code}/sold")
def oc_stock_sold(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        try:
            result = mark_sold(session, code, actor="OPENCLAW")
            session.commit()
            return result
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@oc_stock_router.get("/{code}/delete-preview")
def oc_stock_delete_preview(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        try:
            return delete_preview(session, code)
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@oc_stock_router.delete("/{code}")
def oc_stock_delete(
    code: str,
    confirm: bool = Query(default=False),
    confirm_listings: bool = Query(default=False),
    auth: bool = _AUTH,
) -> Any:
    with get_db_session() as session:
        try:
            # Rule 1: without confirm=true — 409 with delete-preview data.
            if not confirm:
                preview = delete_preview(session, code)
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={
                        "detail": "Подтверждение удаления требуется (confirm=true).",
                        "delete_preview": preview,
                    },
                )
            result = delete_stock_item(session, code, confirm_listings=confirm_listings)
            session.commit()
            return {"status": "DELETED", **result}
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StockDeletionNeedsConfirmationError as exc:
            # Rule 2: confirm=true but no confirm_listings with listings — 409 + listing list.
            preview = delete_preview(session, code)
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": str(exc), "listings": preview["listings"]},
            )
        except LotDeletionBlockedError as exc:
            # Rule 3: active deal — 409 with the can_delete_lot reason.
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": str(exc)},
            )


@oc_stock_router.get("/{code}/photos")
def oc_stock_photos(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        try:
            get_stock_item(session, code)  # 404 when absent
            entries = logical_photo_entries(session, code)
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {
        "item_code": code,
        "photos": [
            {"id": e["id"], "role": e["role"], "is_cover": e["is_cover"], "url": e["url"]}
            for e in entries
        ],
    }


@oc_stock_router.delete("/{code}/photos/{photo_id}")
def oc_stock_photo_delete(code: str, photo_id: str, auth: bool = _AUTH) -> dict[str, Any]:
    from nextlife.media.ops import delete_photo_and_derived

    deleted_ids: list[str] = []
    paths: list[str] = []
    with get_db_session() as session:
        try:
            get_stock_item(session, code)  # 404 when absent
            deleted_ids = _chain_photo_ids(session, code, photo_id)
            paths = delete_photo_and_derived(session, code, photo_id)
            if paths:
                session.commit()
        except StockItemNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # Files are removed only after the commit (owner rule).
    removed = 0
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return {
        "item_code": code,
        "photo_id": photo_id,
        "deleted_ids": deleted_ids,
        "files_removed": removed,
    }
