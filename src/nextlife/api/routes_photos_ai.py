"""API endpoints for AI photo operations and undo (TZ §5.4, §11.1).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from nextlife.api.routes import check_auth, resolve_photo_url
from nextlife.media.ai_edit import (
    ImageAIError,
    ImageAINotConfiguredError,
    ai_remove_dust,
    ai_selling_image,
    is_image_ai_configured,
    undo_photo,
)
from nextlife.obs.audit import record_audit_event
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto

photos_ai_router = APIRouter(tags=["photos-ai"])


def _redirect_stock_photos(
    code: str,
    notice: str | None = None,
    reason: str | None = None,
) -> RedirectResponse:
    url = f"/ui/stock/{code}"
    params: dict[str, str] = {}
    if notice:
        params["notice"] = notice
    if reason:
        params["reason"] = reason
    if params:
        url += "?" + urlencode(params)
    url += "#photos"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


@photos_ai_router.get("/api/photos/ai-status")
def api_get_ai_status(auth: bool = Depends(check_auth)) -> dict[str, Any]:
    """Check whether Image AI EndPoint is configured."""
    return {"configured": is_image_ai_configured()}


@photos_ai_router.post("/api/items/{code}/photos/{photo_id}/ai-dust")
def api_ai_dust(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Generative AI dust removal endpoint."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Предмет {code} не найден")

        photo = session.get(ItemPhoto, photo_id)
        if not photo or photo.item_code != code:
            raise HTTPException(status_code=404, detail=f"Фото {photo_id} не найдено")

        try:
            new_photo = ai_remove_dust(photo_id=photo_id, session=session)
        except ImageAINotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ImageAIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTO_AI_DUST_REMOVED",
            entity="ITEM",
            entity_id=code,
            data={"parent_id": photo_id, "new_id": new_photo.id},
        )
        session.commit()

        return {
            "status": "ok",
            "item_code": code,
            "original_photo_id": photo_id,
            "photo_id": new_photo.id,
            "url": resolve_photo_url(new_photo.path),
            "width": new_photo.width,
            "height": new_photo.height,
            "ai_generated": True,
        }


@photos_ai_router.post("/api/items/{code}/photos/{photo_id}/ai-selling")
def api_ai_selling(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Generative AI clean background and soft shadow endpoint."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Предмет {code} не найден")

        photo = session.get(ItemPhoto, photo_id)
        if not photo or photo.item_code != code:
            raise HTTPException(status_code=404, detail=f"Фото {photo_id} не найдено")

        try:
            new_photo = ai_selling_image(photo_id=photo_id, session=session)
        except ImageAINotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ImageAIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTO_AI_SELLING_IMAGE",
            entity="ITEM",
            entity_id=code,
            data={"parent_id": photo_id, "new_id": new_photo.id},
        )
        session.commit()

        return {
            "status": "ok",
            "item_code": code,
            "original_photo_id": photo_id,
            "photo_id": new_photo.id,
            "url": resolve_photo_url(new_photo.path),
            "width": new_photo.width,
            "height": new_photo.height,
            "ai_generated": True,
        }


@photos_ai_router.post("/api/items/{code}/photos/{photo_id}/undo")
def api_undo_photo(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Revert photo to previous version in chain (§5.4)."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Предмет {code} не найден")

        photo = session.get(ItemPhoto, photo_id)
        if not photo or photo.item_code != code:
            raise HTTPException(status_code=404, detail=f"Фото {photo_id} не найдено")

        parent = undo_photo(session=session, photo_id=photo_id)
        if not parent:
            raise HTTPException(status_code=400, detail="Нет предыдущей версии для отмены")

        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTO_VERSION_REVERTED",
            entity="ITEM",
            entity_id=code,
            data={"reverted_id": photo_id, "restored_id": parent.id},
        )
        session.commit()

        return {
            "status": "ok",
            "item_code": code,
            "reverted_photo_id": photo_id,
            "photo_id": parent.id,
            "url": resolve_photo_url(parent.path),
        }


@photos_ai_router.post("/ui/items/{code}/photos/{photo_id}/ai-dust")
def ui_ai_dust(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    try:
        api_ai_dust(code=code, photo_id=photo_id, auth=auth)
        return _redirect_stock_photos(code, notice="photo_ai_dust_applied")
    except HTTPException as exc:
        return _redirect_stock_photos(code, notice="photo_op_failed", reason=exc.detail)


@photos_ai_router.post("/ui/items/{code}/photos/{photo_id}/ai-selling")
def ui_ai_selling(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    try:
        api_ai_selling(code=code, photo_id=photo_id, auth=auth)
        return _redirect_stock_photos(code, notice="photo_ai_selling_applied")
    except HTTPException as exc:
        return _redirect_stock_photos(code, notice="photo_op_failed", reason=exc.detail)


@photos_ai_router.post("/ui/items/{code}/photos/{photo_id}/undo")
def ui_undo_photo(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    try:
        api_undo_photo(code=code, photo_id=photo_id, auth=auth)
        return _redirect_stock_photos(code, notice="photo_reverted")
    except HTTPException as exc:
        return _redirect_stock_photos(code, notice="photo_op_failed", reason=exc.detail)
