"""Routes for marketplace preview (TZ v2.0 Section IV.3, Rule K-1 <= 400 lines)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import case, select

from nextlife.api.routes import check_auth, resolve_photo_url, templates
from nextlife.core.config import get_media_dir
from nextlife.media.jobs import enqueue_or_get_variant, execute_variant_job, sync_item_media
from nextlife.media.models import MediaMaster, MediaOriginal, MediaVariant
from nextlife.media.pipeline import logical_photo_chains
from nextlife.media.profiles import load_marketplace_media_profile
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item

preview_router = APIRouter(tags=["preview"])

MARKETPLACE_NAMES: dict[str, str] = {
    "bazos_cz": "Bazoš.cz",
    "sbazar_cz": "Sbazar.cz",
    "aukro_cz": "Aukro.cz",
}


@preview_router.get("/ui/items/{code}/preview/{marketplace_key}", response_class=HTMLResponse)
def screen_marketplace_preview(
    request: Request,
    code: str,
    marketplace_key: str,
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render preview of the item as it would appear on the specified marketplace."""
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {code} not found")

        # TZ-NextLife-UI-N2 §4 V3 (P-E08): посевные фото (цепочки item_photo)
        # включаются в конвейер медиа — превью соответствует факту лота.
        sync_item_media(session, code)
        photo_count = len(logical_photo_chains(session, code))

        # Load profile
        profile, _ = load_marketplace_media_profile(marketplace_key)

        # Retrieve media variants for this item and marketplace
        stmt = (
            select(MediaVariant)
            .join(MediaMaster, MediaVariant.master_id == MediaMaster.id)
            .join(MediaOriginal, MediaMaster.original_id == MediaOriginal.id)
            .where(
                MediaOriginal.item_code == code,
                MediaVariant.marketplace_key == marketplace_key,
                MediaVariant.stale.is_(False),
            )
            .order_by(
                case((MediaVariant.role == "COVER", 0), else_=1),
                MediaVariant.created_at.asc(),
            )
        )
        variants_db = session.execute(stmt).scalars().all()

        # If variants missing, generate them on the fly
        if not variants_db:
            masters = (
                session.execute(
                    select(MediaMaster)
                    .join(MediaOriginal, MediaMaster.original_id == MediaOriginal.id)
                    .where(MediaOriginal.item_code == code)
                )
                .scalars()
                .all()
            )
            for m in masters:
                _, job, _ = enqueue_or_get_variant(
                    session=session,
                    master_id=m.id,
                    marketplace_key=marketplace_key,
                    role="COVER" if m.is_cover else "GALLERY",
                    storage_dir=get_media_dir() / "variants",
                )
                if job:
                    execute_variant_job(session, job)
            variants_db = session.execute(stmt).scalars().all()

        variants_data = [
            {
                "id": v.id,
                "role": v.role,
                "url": resolve_photo_url(v.file_path),
                "width": v.width,
                "height": v.height,
                "file_bytes": v.file_bytes,
                "sha256": v.sha256,
            }
            for v in variants_db
        ]

        item_data = {
            "code": item.code,
            "title": item.title,
            "status": item.status,
            "target_price": item.target_price,
            "target_currency": item.target_currency,
        }

        # TZ-NextLife-UI-N2 §3.3 P-E06: предпросмотр показывает финальный
        # публикуемый текст (единый источник правды get_item_text_blocks),
        # а не устаревший attrs.description.
        from nextlife.services.draft_text import get_item_text_blocks

        desc = get_item_text_blocks(item)["final_text"]
        mp_name = MARKETPLACE_NAMES.get(marketplace_key, marketplace_key.capitalize())

        return templates.TemplateResponse(
            request=request,
            name="marketplace_preview.html",
            context={
                "item": item_data,
                "marketplace_key": marketplace_key,
                "marketplace_name": mp_name,
                "profile": profile,
                "variants": variants_data,
                "photo_count": photo_count,
                "description": desc,
            },
        )
