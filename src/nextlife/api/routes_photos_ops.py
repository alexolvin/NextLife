"""API endpoints for photo edit operations: reorder, cover, rotate, crop."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from nextlife.api.routes import check_auth, resolve_photo_url
from nextlife.media.jobs import regenerate_item_variants
from nextlife.media.ops import (
    PhotoOpForbiddenError,
    clean_background_photo,
    crop_photo,
    remove_dust_photo,
    rotate_photo,
    toggle_defect_role,
)
from nextlife.obs.audit import record_audit_event
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto

photos_ops_router = APIRouter(tags=["photos-ops"])


class ReorderPhotosRequest(BaseModel):
    order: list[str] = []


class RotatePhotoRequest(BaseModel):
    degrees: int = 90
    photo_id: str | None = None


class CropPhotoRequest(BaseModel):
    box: list[int]
    photo_id: str | None = None


@photos_ops_router.post("/api/items/{code}/photos/reorder")
def api_reorder_item_photos(
    code: str,
    payload: ReorderPhotosRequest,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Reorder photos of an item."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        attrs = dict(item.attrs or {})
        attrs["photo_order"] = payload.order
        item.attrs = attrs

        for idx, pid in enumerate(payload.order):
            photo = session.get(ItemPhoto, pid)
            if photo and photo.item_code == code:
                if isinstance(photo.transform_log, list):
                    photo.transform_log = list(photo.transform_log) + [{"step": "reorder", "sort_order": idx}]
                elif isinstance(photo.transform_log, dict):
                    t_log = dict(photo.transform_log)
                    t_log["sort_order"] = idx
                    photo.transform_log = t_log
                else:
                    photo.transform_log = [{"step": "reorder", "sort_order": idx}]

        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTOS_REORDERED",
            entity="ITEM",
            entity_id=code,
            data={"count": len(payload.order), "order": payload.order},
        )
        session.commit()

        return {
            "status": "ok",
            "item_code": code,
            "order": payload.order,
        }


@photos_ops_router.post("/api/items/{code}/photos/{photo_id}/cover")
def api_set_item_photo_cover(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Set specified photo as cover photo for the item."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        photo = session.get(ItemPhoto, photo_id)
        if not photo or photo.item_code != code:
            raise HTTPException(status_code=404, detail=f"Photo {photo_id} not found for item {code}")

        attrs = dict(item.attrs or {})
        attrs["cover_photo_id"] = photo_id

        existing_order = [p for p in (attrs.get("photo_order") or []) if p != photo_id]
        attrs["photo_order"] = [photo_id] + existing_order
        item.attrs = attrs

        if isinstance(photo.transform_log, list):
            photo.transform_log = list(photo.transform_log) + [{"step": "set_cover"}]
        elif isinstance(photo.transform_log, dict):
            t_log = dict(photo.transform_log)
            t_log["is_cover"] = True
            photo.transform_log = t_log
        else:
            photo.transform_log = [{"step": "set_cover"}]

        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTO_SET_COVER",
            entity="ITEM",
            entity_id=code,
            data={"photo_id": photo_id},
        )
        session.commit()

        return {
            "status": "ok",
            "item_code": code,
            "cover_photo_id": photo_id,
        }


@photos_ops_router.post("/ui/items/{code}/photos/{photo_id}/cover")
def ui_set_item_photo_cover(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Set photo as cover and redirect to item detail."""
    try:
        api_set_item_photo_cover(code=code, photo_id=photo_id, auth=auth)
    except HTTPException as exc:
        return _redirect_card(code, notice="photo_op_failed", reason=exc.detail)
    return _redirect_card(code, notice="photo_cover_set")


@photos_ops_router.post("/api/items/{code}/photos/rotate")
def api_rotate_item_photo(
    code: str,
    payload: RotatePhotoRequest,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Rotate item photo by specified degrees."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        photo = rotate_photo(
            session=session,
            item_code=code,
            photo_id=payload.photo_id,
            degrees=payload.degrees,
        )
        if not photo:
            raise HTTPException(status_code=404, detail=f"No photos found to rotate for item {code}")

        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTO_ROTATED",
            entity="ITEM",
            entity_id=code,
            data={
                "photo_id": photo.id,
                "degrees": payload.degrees,
                "width": photo.width,
                "height": photo.height,
            },
        )
        session.commit()

        return {
            "status": "ok",
            "item_code": code,
            "photo_id": photo.id,
            "degrees": payload.degrees,
            "url": resolve_photo_url(photo.path),
            "width": photo.width,
            "height": photo.height,
        }


@photos_ops_router.post("/api/items/{code}/photos/crop")
def api_crop_item_photo(
    code: str,
    payload: CropPhotoRequest,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Crop item photo by specified bounding box [left, top, right, bottom]."""
    if len(payload.box) != 4 or payload.box[2] <= payload.box[0] or payload.box[3] <= payload.box[1]:
        raise HTTPException(status_code=422, detail="Invalid crop bounding box")

    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        photo = crop_photo(
            session=session,
            item_code=code,
            box=payload.box,
            photo_id=payload.photo_id,
        )
        if not photo:
            raise HTTPException(status_code=404, detail=f"No photos found to crop for item {code}")

        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTO_CROPPED",
            entity="ITEM",
            entity_id=code,
            data={
                "photo_id": photo.id,
                "box": payload.box,
                "width": photo.width,
                "height": photo.height,
            },
        )
        session.commit()

        return {
            "status": "ok",
            "item_code": code,
            "photo_id": photo.id,
            "box": payload.box,
            "url": resolve_photo_url(photo.path),
            "width": photo.width,
            "height": photo.height,
        }


# Per-photo UI operations (TZ-NextLife-UI-N2 §4 V6, R5).
def _redirect_card(
    code: str, notice: str | None = None, reason: str | None = None, fragment: str = "photos"
) -> RedirectResponse:
    """Redirect to the item card, optionally with a toast notice (?notice=…&reason=…#photos)."""
    url = f"/ui/stock/{code}"
    params: dict[str, str] = {}
    if notice:
        params["notice"] = notice
    if reason:
        params["reason"] = reason
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    if fragment:
        url += f"#{fragment}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


def _require_item(session: Session, code: str) -> None:
    if not session.get(Item, code):
        raise HTTPException(status_code=404, detail=f"Item {code} not found")


@photos_ops_router.post("/ui/items/{code}/photos/{photo_id}/rotate")
async def ui_rotate_item_photo(
    request: Request,
    code: str,
    photo_id: str,
    degrees: int = 90,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """UI: поворот кадра на 90° (вправо: 90, влево: -90 / 270) — пер-фото кнопка (R5)."""
    try:
        form = await request.form()
        deg_val = form.get("degrees")
        if deg_val is not None:
            degrees = int(deg_val)
    except Exception:
        pass
    with get_db_session() as session:
        _require_item(session, code)
        photo = rotate_photo(session=session, item_code=code, photo_id=photo_id, degrees=degrees)
        if not photo:
            return _redirect_card(code, notice="photo_op_failed", reason="Фото не найдено")
        record_audit_event(
            session=session, actor="USER", action="PHOTO_ROTATED", entity="ITEM",
            entity_id=code, data={"photo_id": photo.id, "degrees": degrees},
        )
        session.commit()
    return _redirect_card(code, notice="photo_rotated")


@photos_ops_router.post("/ui/items/{code}/photos/{photo_id}/crop")
def ui_crop_item_photo(
    code: str, photo_id: str, auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """UI: обрезка 10% от поля — коробка считается из текущих размеров."""
    with get_db_session() as session:
        _require_item(session, code)
        row = session.get(ItemPhoto, photo_id)
        if not row or row.item_code != code:
            raise HTTPException(status_code=404, detail=f"Photo {photo_id} not found for item {code}")
        w, h = int(row.width or 0), int(row.height or 0)
        box = [w // 10, h // 10, (w * 9) // 10, (h * 9) // 10]
        if box[2] <= box[0] or box[3] <= box[1]:
            raise HTTPException(status_code=422, detail="Photo too small for crop")
        photo = crop_photo(session=session, item_code=code, box=box, photo_id=photo_id)
        if not photo:
            raise HTTPException(status_code=404, detail=f"No photos found to crop for item {code}")
        record_audit_event(
            session=session, actor="USER", action="PHOTO_CROPPED", entity="ITEM",
            entity_id=code, data={"photo_id": photo.id, "box": box},
        )
        session.commit()
    return _redirect_card(code)


@photos_ops_router.post("/ui/items/{code}/photos/{photo_id}/remove-dust")
def ui_remove_dust_item_photo(
    code: str, photo_id: str, auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """UI: убрать пыль (R5). Для фото дефекта — 400 (честность)."""
    with get_db_session() as session:
        _require_item(session, code)
        try:
            photo = remove_dust_photo(session=session, item_code=code, photo_id=photo_id)
        except PhotoOpForbiddenError as exc:
            return _redirect_card(code, notice="photo_op_failed", reason=str(exc))
        if not photo:
            return _redirect_card(code, notice="photo_op_failed", reason="Фото не найдено")
        record_audit_event(
            session=session, actor="USER", action="PHOTO_DUST_REMOVED", entity="ITEM",
            entity_id=code, data={"photo_id": photo.id},
        )
        session.commit()
    return _redirect_card(code, notice="photo_dust_removed")


@photos_ops_router.post("/ui/items/{code}/photos/{photo_id}/clean-bg")
def ui_clean_bg_item_photo(
    code: str, photo_id: str, auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """UI: очистить фон (R5). Для фото дефекта — 400 (честность)."""
    with get_db_session() as session:
        _require_item(session, code)
        try:
            photo = clean_background_photo(session=session, item_code=code, photo_id=photo_id)
        except PhotoOpForbiddenError as exc:
            return _redirect_card(code, notice="photo_op_failed", reason=str(exc))
        if not photo:
            return _redirect_card(code, notice="photo_op_failed", reason="Фото не найдено")
        record_audit_event(
            session=session, actor="USER", action="PHOTO_BACKGROUND_CLEANED", entity="ITEM",
            entity_id=code, data={"photo_id": photo.id},
        )
        session.commit()
    return _redirect_card(code, notice="photo_bg_cleaned")


@photos_ops_router.post("/ui/items/{code}/photos/{photo_id}/defect")
def ui_toggle_item_photo_defect(
    code: str, photo_id: str, auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """UI: пометка «фото дефекта» (DEFECT <-> PUBLISH)."""
    with get_db_session() as session:
        _require_item(session, code)
        photo = toggle_defect_role(session=session, item_code=code, photo_id=photo_id)
        if not photo:
            return _redirect_card(code, notice="photo_op_failed", reason="Фото не найдено")
        record_audit_event(
            session=session, actor="USER", action="PHOTO_DEFECT_TOGGLED", entity="ITEM",
            entity_id=code, data={"photo_id": photo.id, "role": photo.role},
        )
        session.commit()
    return _redirect_card(code, notice="photo_defect_toggled")


@photos_ops_router.post("/ui/items/{code}/photos/regen-variants")
def ui_regen_item_photo_variants(
    code: str, auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """UI: догенерация вариантов под каждую площадку (TZ N2 §4 V6)."""
    with get_db_session() as session:
        _require_item(session, code)
        count = regenerate_item_variants(session, code)
        record_audit_event(
            session=session, actor="USER", action="PHOTO_VARIANTS_REGENERATED",
            entity="ITEM", entity_id=code, data={"count": count},
        )
        session.commit()
    return _redirect_card(code, notice="photo_variants_regenerated", reason=str(count))
