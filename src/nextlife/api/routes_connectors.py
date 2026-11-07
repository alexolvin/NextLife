"""Connect console, Facebook package, owner confirmation, connector report."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select

from nextlife.api.routes import check_auth, templates
from nextlife.connectors.ops import storage_state_path
from nextlife.core.status import ListingStatus
from nextlife.services.facebook_pack import build_zip
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto, Listing

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONNECT_BINARIES = ("Xvfb", "x11vnc", "websockify")

PLATFORMS_META: dict[str, dict[str, Any]] = {
    "bazar_cz": {
        "title": "Bazar.cz",
        "type": "web",
        "login_url": "https://www.bazar.cz/prihlaseni",
        "description": "Чешская доска объявлений Bazar.cz",
        "hint": "Войдите в аккаунт в браузере и сохраните storage_state.json",
    },
    "aukro_cz": {
        "title": "Aukro.cz",
        "type": "web",
        "login_url": "https://aukro.cz/uzivatel/prihlaseni",
        "description": "Крупнейший чешский аукцион и торговая площадка",
        "hint": "Войдите на Aukro.cz и сохраните сессию для автоматической публикации",
    },
    "sbazar_cz": {
        "title": "SBazar.cz",
        "type": "both",
        "login_url": "https://login.szn.cz/?service=sbazar",
        "phone_platform": "sbazar_cz",
        "description": "SBazar.cz от Seznam.cz (веб-сессия или приложение на телефоне)",
        "hint": "Приложение установлено на телефоне, либо можно загрузить веб-сессию",
    },
    "mimibazar_cz": {
        "title": "Mimibazar.cz",
        "type": "web",
        "login_url": "https://www.mimibazar.cz/prihlaseni",
        "description": "Семейная и детская барахолка Mimibazar.cz",
        "hint": "Требуется веб-сессия для публикации",
    },
    "trhknih_cz": {
        "title": "TrhKnih.cz",
        "type": "web",
        "login_url": "https://www.trhknih.cz/prihlasit",
        "description": "Книжный маркетплейс TrhKnih.cz",
        "hint": "Требуется веб-сессия для публикации книг",
    },
    "vinted": {
        "title": "Vinted.cz",
        "type": "both",
        "login_url": "https://www.vinted.cz/member/general/login",
        "phone_platform": "vinted",
        "description": "Vinted (одежда, обувь, аксессуары)",
        "hint": "Доступно приложение на телефоне или веб-сессия",
    },
    "facebook_marketplace": {
        "title": "Facebook Marketplace",
        "type": "both",
        "login_url": "https://www.facebook.com/login",
        "phone_platform": "facebook_marketplace",
        "description": "Facebook Marketplace (через приложение Facebook или веб)",
        "hint": "Вход через приложение Facebook на телефоне или веб-сессию",
    },
}


def _missing_connect_binaries() -> list[str]:
    return [name for name in _CONNECT_BINARIES if shutil.which(name) is None]


@router.get("/ui/connect", response_class=HTMLResponse)
def screen_connect(request: Request, platform: str = "", auth: bool = Depends(check_auth)) -> HTMLResponse:
    missing = _missing_connect_binaries()
    current_key = (platform or "bazar_cz").strip().lower()
    if current_key not in PLATFORMS_META:
        current_key = "bazar_cz"

    platforms_list = []
    for key, meta in PLATFORMS_META.items():
        st_path = storage_state_path(key)
        has_storage = st_path.is_file() and st_path.stat().st_size > 20
        mtime_str = ""
        if has_storage:
            mtime_str = datetime.fromtimestamp(st_path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d %H:%M")
        platforms_list.append(
            {
                "key": key,
                "title": meta["title"],
                "type": meta["type"],
                "login_url": meta["login_url"],
                "description": meta["description"],
                "hint": meta["hint"],
                "has_storage": has_storage,
                "mtime": mtime_str,
                "is_active": key == current_key,
                "phone_platform": meta.get("phone_platform"),
            }
        )

    current_meta = next(p for p in platforms_list if p["key"] == current_key)

    return templates.TemplateResponse(
        request=request,
        name="connect.html",
        context={
            "request": request,
            "active_tab": "connectors",
            "platform": current_key,
            "current_platform": current_meta,
            "platforms": platforms_list,
            "missing_binaries": missing,
            "chrome": shutil.which("google-chrome") or shutil.which("chromium-browser") or "",
            "ready": not missing,
            "notice": request.query_params.get("notice", ""),
        },
    )


@router.post("/ui/connect/{platform}/open-phone")
def open_platform_on_phone(platform: str, auth: bool = Depends(check_auth)) -> RedirectResponse:
    from nextlife.android.transport import make_phone_broker_transport
    from nextlife.core.config import load_config

    meta = PLATFORMS_META.get(platform)
    phone_key = meta.get("phone_platform") if meta else platform
    if not phone_key:
        phone_key = platform

    try:
        cfg = load_config().app.android_bridge.phone_broker
        transport = make_phone_broker_transport(cfg)
        transport.begin(phone_key, "interactive")
        transport.open_app()
        transport.end()
        notice = "phone_opened"
    except Exception as exc:
        notice = f"phone_error_{exc}"

    return RedirectResponse(
        url=f"/ui/connect?platform={platform}&notice={notice}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/ui/connectors", response_class=HTMLResponse)
def screen_connectors_map(request: Request, auth: bool = Depends(check_auth)) -> HTMLResponse:
    from nextlife.services.connectors_map import (
        STATE_NEEDS_OWNER,
        STATE_SEARCH_ONLY,
        STATE_UNRESOLVED,
        STATE_WORKING,
        compute_connectors_map,
        format_owner_message,
    )
    with get_db_session() as session:
        statuses = compute_connectors_map(session)
        owner_message = format_owner_message(statuses)

    working = [s for s in statuses if s.state == STATE_WORKING]
    needs_owner = [s for s in statuses if s.state == STATE_NEEDS_OWNER]
    unresolved = [s for s in statuses if s.state == STATE_UNRESOLVED]
    search_only = [s for s in statuses if s.state == STATE_SEARCH_ONLY]

    return templates.TemplateResponse(
        request=request,
        name="connectors.html",
        context={
            "request": request,
            "active_tab": "connectors",
            "working_list": working,
            "owner_list": needs_owner,
            "unresolved_list": unresolved,
            "search_only_list": search_only,
            "owner_message": owner_message,
        },
    )


@router.post("/ui/connectors/check")
def action_connectors_check(auth: bool = Depends(check_auth)) -> RedirectResponse:
    from nextlife.services.connectors_map import run_regular_check
    with get_db_session() as session:
        run_regular_check(session)
    return RedirectResponse(url="/ui/connectors?notice=check_completed", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/ui/connect/{platform}/storage")
async def save_storage_state(
    platform: str,
    request: Request,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Owner pastes or uploads a Playwright storage state after logging in themselves."""
    import json

    content_type = request.headers.get("content-type", "")
    raw_content = b""

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        text_content = str(form_data.get("storage_json") or "").strip()
        uploaded_file = form_data.get("storage_file")
        if text_content:
            raw_content = text_content.encode("utf-8")
        elif uploaded_file and hasattr(uploaded_file, "read"):
            raw_content = await uploaded_file.read()
    else:
        raw_content = await request.body()

    if not raw_content or len(raw_content) > 5_000_000:
        return RedirectResponse(
            url=f"/ui/connect?platform={platform}&notice=error_empty",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Validate JSON
    try:
        json.loads(raw_content.decode("utf-8", errors="replace"))
    except Exception:
        return RedirectResponse(
            url=f"/ui/connect?platform={platform}&notice=error_invalid_json",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    path = storage_state_path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw_content)
    os.chmod(path, 0o600)
    return RedirectResponse(
        url=f"/ui/connect?platform={platform}&notice=saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/ui/connectors/report", response_class=HTMLResponse)
def screen_report(request: Request, auth: bool = Depends(check_auth)) -> HTMLResponse:
    path = _REPO_ROOT / "reports" / "final.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else "Отчёт ещё не собран."
    return templates.TemplateResponse(
        request=request,
        name="connectors_report.html",
        context={"request": request, "active_tab": "settings", "report_text": text},
    )


@router.get("/ui/stock/{code}/facebook-pack.zip")
def download_facebook_pack(code: str, auth: bool = Depends(check_auth)) -> FileResponse:
    with get_db_session() as session:
        item = session.get(Item, code)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Предмет #{code} не найден")
        photos = session.scalars(
            select(ItemPhoto).where(ItemPhoto.item_code == code, ItemPhoto.role == "PUBLISH")
        ).all()
        if not photos:
            photos = session.scalars(
                select(ItemPhoto).where(ItemPhoto.item_code == code, ItemPhoto.role == "ORIGINAL")
            ).all()
        paths = []
        for row in photos:
            if not row.path:
                continue
            candidate = Path(row.path)
            try:
                readable = candidate.is_file()
            except OSError:
                readable = False
            if readable:
                paths.append(candidate)
        title = item.title or code
        price = str(item.target_price or "")
        currency = item.target_currency or ""
        description = item.description or ""
    dest = _REPO_ROOT / "evidence" / "facebook_marketplace" / "pack" / f"{code}.zip"
    if dest.exists():
        dest = _REPO_ROOT / "evidence" / "facebook_marketplace" / "pack" / f"{code}-{os.getpid()}.zip"
    build_zip(dest, title=title, price=price, currency=currency, description=description, photo_paths=paths)
    return FileResponse(dest, media_type="application/zip", filename=f"facebook-{code}.zip")


@router.post("/ui/listings/{listing_id}/owner-accept")
def owner_accept(
    listing_id: str,
    return_to: str = Form("/ui/connectors/report"),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    with get_db_session() as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="Объявление не найдено")
        evidence = dict(listing.publish_evidence or {})
        evidence["live_status"] = "ACCEPTED_BY_OWNER"
        listing.publish_evidence = evidence
        if listing.status == ListingStatus.DRAFT.value:
            listing.status = ListingStatus.APPROVED.value
        session.commit()
    if not return_to.startswith("/ui/"):
        return_to = "/ui/connectors/report"
    return RedirectResponse(url=return_to, status_code=status.HTTP_303_SEE_OTHER)
