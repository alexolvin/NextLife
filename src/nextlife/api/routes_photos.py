"""API routes for photo management: upload, process, delete.

TZ v2.0 Section V.1, VI.1, Rule VI.1.1 (<= 400 lines).
"""

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.api.routes import check_auth, resolve_photo_url
from nextlife.api.routes_photos_ai import photos_ai_router
from nextlife.api.routes_photos_ops import photos_ops_router
from nextlife.core.config import get_media_dir
from nextlife.media.jobs import sync_tier_models_and_generate_variants
from nextlife.media.pipeline import (
    delete_photo_and_derived,
    deterministic_process,
    ingest_photo,
    sanitize_photo,
)
from nextlife.media.quality import analyze_photo_quality
from nextlife.obs.audit import record_audit_event
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto

photos_router = APIRouter(tags=["photos"])
photos_router.include_router(photos_ops_router)
photos_router.include_router(photos_ai_router)


def _save_and_process_upload(
    session: Session,
    item_code: str,
    upload: UploadFile,
    role: str = "ORIGINAL",
) -> ItemPhoto:
    """Save an uploaded file and process it through the media pipeline."""
    temp_dir = get_media_dir() / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    filename = upload.filename or f"upload_{uuid.uuid4().hex[:6]}.jpg"
    temp_path = temp_dir / f"tmp_{uuid.uuid4().hex}_{filename}"

    try:
        with open(temp_path, "wb") as f_out:
            shutil.copyfileobj(upload.file, f_out)

        orig_photo = ingest_photo(
            session=session,
            item_code=item_code,
            src_path=temp_path,
            role=role,
        )
        # TZ-NextLife-UI-N2 §4 V6 (R4): подсказки по качеству сразу
        # после загрузки (поворот/тёмный/мелкий/размыт).
        hints = analyze_photo_quality(orig_photo.path)
        if hints:
            orig_photo.vlm_verdict = {
                "verified": True,
                "reason": "Quality check (deterministic)",
                "hints": hints,
            }
        clean_photo = sanitize_photo(session=session, original=orig_photo)
        proc_photo = deterministic_process(session=session, photo=clean_photo)
        sync_tier_models_and_generate_variants(
            session=session,
            item_code=item_code,
            orig_photo=orig_photo,
            proc_photo=proc_photo,
            role=role,
        )
        return orig_photo
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _process_uploads(
    session: Session,
    code: str,
    files: list[UploadFile],
    role: str,
) -> list[str]:
    """Ingest, sanitize, and resize a batch of uploaded files."""
    ids: list[str] = []
    for file in files:
        if file.filename:
            p = _save_and_process_upload(session, code, file, role=role)
            ids.append(p.id)
    return ids


@photos_router.post("/api/items/{code}/photos")
def api_upload_item_photos(
    code: str,
    files: list[UploadFile] | None = File(None),  # noqa: B008
    file: UploadFile | None = File(None),  # noqa: B008
    role: str = Form("ORIGINAL"),
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Upload photos via JSON API, sanitize, resize and return metadata."""
    upload_list: list[UploadFile] = []
    if files:
        upload_list.extend(files)
    if file:
        upload_list.append(file)
    if not upload_list:
        raise HTTPException(status_code=422, detail="At least one file must be provided")

    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        uploaded_ids = _process_uploads(session, code, upload_list, role)
        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTOS_UPLOADED",
            entity="ITEM",
            entity_id=code,
            data={"count": len(uploaded_ids), "ids": uploaded_ids, "role": role},
        )
        session.commit()

        photos = (
            session.execute(select(ItemPhoto).where(ItemPhoto.item_code == code))
            .scalars()
            .all()
        )
        return {
            "status": "ok",
            "item_code": code,
            "uploaded_count": len(uploaded_ids),
            "photos": [
                {
                    "id": p.id,
                    "role": p.role,
                    "url": resolve_photo_url(p.path),
                    "width": p.width,
                    "height": p.height,
                }
                for p in photos
            ],
        }


@photos_router.post("/ui/items/{code}/photos/upload")
def ui_upload_item_photos(
    code: str,
    files: list[UploadFile] = File(...),  # noqa: B008
    role: str = Form("ORIGINAL"),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Upload photos from item detail card in Web UI."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        uploaded_ids = _process_uploads(session, code, files, role)
        record_audit_event(
            session=session,
            actor="USER",
            action="PHOTOS_UPLOADED",
            entity="ITEM",
            entity_id=code,
            data={"count": len(uploaded_ids), "ids": uploaded_ids, "role": role},
        )
        session.commit()

    return RedirectResponse(
        url=f"/ui/stock/{code}?notice=photos_uploaded#photos",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@photos_router.post("/ui/items/{code}/photos/{photo_id}/delete")
def ui_delete_item_photo(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Delete photo and its derived files."""
    paths: list[str] = []
    with get_db_session() as session:
        paths = delete_photo_and_derived(session, code, photo_id)
        if paths:
            record_audit_event(
                session=session,
                actor="USER",
                action="PHOTO_DELETED",
                entity="ITEM",
                entity_id=code,
                data={"photo_id": photo_id},
            )
            session.commit()

    # Файлы — только после успешного коммита: откат не должен оставлять
    # строки в БД без файлов (ошибка 3f68648f215e).
    for p in paths:
        p_file = Path(p)
        if p_file.exists():
            try:
                p_file.unlink()
            except OSError:
                pass

    # Э-2: баннер «удалено» — только после подтверждённого удаления;
    # пустой результат (фото не найдено) честно сообщает «не найдено»
    # (P-E17, финальная приёмка N2).
    notice = "photo_deleted" if paths else "photo_not_found"
    return RedirectResponse(
        url=f"/ui/stock/{code}?notice={notice}#photos",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@photos_router.post("/ui/wizard/{code}/photos/upload")
def ui_wizard_upload_photos(
    code: str,
    files: list[UploadFile] = File(...),  # noqa: B008
    role: str = Form("ORIGINAL"),
    auth: bool = Depends(check_auth),
) -> Any:
    """Upload photos in Wizard Step 2."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        _process_uploads(session, code, files, role)
        session.commit()

    return RedirectResponse(
        url=f"/ui/wizard/{code}?step=2&notice=photos_uploaded",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@photos_router.post("/ui/wizard/{code}/photos/{photo_id}/delete")
def ui_wizard_delete_photo(
    code: str,
    photo_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Delete photo in Wizard Step 2."""
    paths: list[str] = []
    with get_db_session() as session:
        paths = delete_photo_and_derived(session, code, photo_id)
        if paths:
            session.commit()

    for p in paths:
        p_file = Path(p)
        if p_file.exists():
            try:
                p_file.unlink()
            except OSError:
                pass

    return RedirectResponse(
        url=f"/ui/wizard/{code}?step=2&notice=photo_deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )
