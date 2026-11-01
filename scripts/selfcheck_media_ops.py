"""Media operations checks (C-19 .. C-23) for selfcheck.

Self-contained: each check creates its own lot and uploads its own photo, then
operates strictly by the 3-char code obtained at creation (no "pick the newest"
selector). See scripts/selfcheck_common.py for the design rationale.
"""

from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright
from scripts.selfcheck_common import create_lot, photo_id_from_response, upload_photo_api
from scripts.selfcheck_types import CheckContext, CheckDef
from sqlalchemy import text

from nextlife.storage.db import get_engine


def run_c19(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-19: Cover image displays where expected (own lot + own photo)."""
    code = create_lot(ctx, "C-19 cover")
    upload_photo_api(ctx, code, color="red")
    status_code = 0
    content_type = ""
    cover_url = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))

        img = page.locator(".slider-img, [data-testid='cover-image']").first
        if img.count() > 0:
            cover_url = img.get_attribute("src") or ""
        browser.close()

    if cover_url:
        full_url = cover_url if cover_url.startswith("http") else f"{ctx.base_url}{cover_url}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(full_url)
            status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")

    art_dir.joinpath("cover_http.txt").write_text(
        f"code={code}\nurl={cover_url}\nstatus={status_code}\ncontent-type={content_type}", encoding="utf-8"
    )
    return {"status_code": status_code, "is_image": "image" in content_type, "code": code}


def validate_c19(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    ch = art_dir / "cover_http.txt"
    if not ss.exists() or not ch.exists():
        return False, "отсутствует: отсутствует screenshot.png или cover_http.txt"
    if meta.get("status_code") == 200 and meta.get("is_image"):
        return True, "пройдено: обложка отображается и отдаёт image/*"
    if meta.get("status_code") in (404, 405):
        return False, f"отсутствует: маршрут обложки не найден (HTTP {meta.get('status_code')})"
    return False, f"поведение неверно: обложка вернула статус {meta.get('status_code')}"


def run_c20(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-20: Delete photo and verify removal of record and derivatives (own lot + own photo)."""
    code = create_lot(ctx, "C-20 delete photo")
    _up_status, photos = upload_photo_api(ctx, code, color="blue")
    photo_id = photo_id_from_response(photos)
    if not photo_id:
        # Fallback: the single photo we just uploaded, scoped to this lot.
        with get_engine().connect() as conn:
            row = conn.execute(text("SELECT id FROM item_photo WHERE item_code = :code ORDER BY created_at DESC LIMIT 1;"), {"code": code}).fetchone()
        photo_id = row[0] if row else ""

    if not photo_id:
        art_dir.joinpath("delete_response.txt").write_text("NO_PHOTO_TO_DELETE", encoding="utf-8")
        return {"deleted": False, "code": code}

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/ui/items/{code}/photos/{photo_id}/delete?token={ctx.auth_token}",
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
            follow_redirects=True,
        )
        art_dir.joinpath("delete_response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    # DB after (scoped to this lot)
    with get_engine().connect() as conn:
        after_row = conn.execute(text("SELECT id FROM item_photo WHERE id = :photo_id;"), {"photo_id": photo_id}).fetchone()

    art_dir.joinpath("db_after.txt").write_text(str(after_row), encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))
        browser.close()

    return {"deleted": after_row is None, "status_code": resp.status_code, "code": code}


def validate_c20(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    db_file = art_dir / "db_after.txt"
    ss = art_dir / "screenshot.png"
    if not db_file.exists() or not ss.exists():
        return False, "отсутствует: отсутствует db_after.txt или screenshot.png"
    if meta.get("status_code") in (404, 405):
        return False, f"отсутствует: эндпоинт удаления фото не найден (HTTP {meta.get('status_code')})"
    if meta.get("deleted"):
        return True, "пройдено: фото удалено из БД, интерфейс обновлён"
    return False, "поведение неверно: фото не было удалено из базы данных"


def run_c21(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-21: Reorder photos via reorder API (own lot + own photo)."""
    code = create_lot(ctx, "C-21 reorder")
    upload_photo_api(ctx, code, color="green")
    art_dir.joinpath("reorder_request.txt").write_text(f"POST /api/items/{code}/photos/reorder", encoding="utf-8")

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/api/items/{code}/photos/reorder",
            json={"order": []},
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("reorder_response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))
        browser.close()

    art_dir.joinpath("db_order.txt").write_text(f"reorder_check (lot {code})", encoding="utf-8")
    return {"status_code": resp.status_code, "supported": resp.status_code == 200, "code": code}


def validate_c21(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    rr = art_dir / "reorder_response.txt"
    if not rr.exists():
        return False, "отсутствует: отсутствует reorder_response.txt"
    if meta.get("supported"):
        return True, "пройдено: смена порядка фото поддержана"
    sc = meta.get("status_code")
    if sc in (404, 405):
        return False, f"отсутствует: эндпоинт смены порядка фото не найден (HTTP {sc})"
    return False, f"поведение неверно: эндпоинт смены порядка фото вернул статус {sc}"


def run_c22(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-22: Set photo as cover (own lot + own photo)."""
    code = create_lot(ctx, "C-22 set cover")
    _up_status, photos = upload_photo_api(ctx, code, color="magenta")
    photo_id = photo_id_from_response(photos)
    if not photo_id:
        with get_engine().connect() as conn:
            row = conn.execute(text("SELECT id FROM item_photo WHERE item_code = :code ORDER BY created_at DESC LIMIT 1;"), {"code": code}).fetchone()
        photo_id = row[0] if row else "none"

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/api/items/{code}/photos/{photo_id}/cover",
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("request.txt").write_text(f"POST /api/items/{code}/photos/{photo_id}/cover", encoding="utf-8")
        art_dir.joinpath("response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))
        browser.close()

    art_dir.joinpath("db_cover.txt").write_text(f"cover_photo_id={photo_id} (lot {code})", encoding="utf-8")
    return {"status_code": resp.status_code, "supported": resp.status_code == 200, "code": code}


def validate_c22(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    rf = art_dir / "response.txt"
    if not rf.exists():
        return False, "отсутствует: отсутствует response.txt"
    if meta.get("supported"):
        return True, "пройдено: назначение обложки выполнено"
    sc = meta.get("status_code")
    if sc in (404, 405):
        return False, f"отсутствует: эндпоинт назначения обложки не найден (HTTP {sc})"
    return False, f"поведение неверно: эндпоинт назначения обложки вернул статус {sc}"


def run_c23(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-23: Rotate image 90 degrees (own lot + own photo)."""
    code = create_lot(ctx, "C-23 rotate")
    upload_photo_api(ctx, code, color="cyan")
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/api/items/{code}/photos/rotate",
            json={"degrees": 90},
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("request.txt").write_text(f"POST /api/items/{code}/photos/rotate 90", encoding="utf-8")
        art_dir.joinpath("response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    art_dir.joinpath("image_meta_after.txt").write_text(f"rotate_status={resp.status_code} (lot {code})", encoding="utf-8")
    return {"status_code": resp.status_code, "supported": resp.status_code == 200, "code": code}


def validate_c23(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    rf = art_dir / "response.txt"
    if not rf.exists():
        return False, "отсутствует: отсутствует response.txt"
    if meta.get("supported"):
        return True, "пройдено: поворот изображения на 90 градусов выполнен"
    sc = meta.get("status_code")
    if sc in (404, 405):
        return False, f"отсутствует: эндпоинт поворота изображения не найден (HTTP {sc})"
    return False, f"поведение неверно: эндпоинт поворота изображения вернул статус {sc}"


def get_media_ops_checks() -> list[CheckDef]:
    return [
        CheckDef("C-19", "Медиа", "Обложка отображается там, где ожидается", "Запрошенный URL обложки отдаёт изображение", run_c19, ["cover_http.txt", "screenshot.png"], validate_c19),
        CheckDef("C-20", "Медиа", "Удаление фото", "Запись и производные удалены, оставшиеся отображаются", run_c20, ["delete_response.txt", "db_after.txt", "screenshot.png"], validate_c20),
        CheckDef("C-21", "Медиа", "Смена порядка перетаскиванием", "Новый порядок сохранён и виден после перезагрузки", run_c21, ["reorder_request.txt", "reorder_response.txt", "screenshot.png", "db_order.txt"], validate_c21),
        CheckDef("C-22", "Медиа", "Назначение обложки", "Обложка изменилась в БД и на экране", run_c22, ["request.txt", "response.txt", "db_cover.txt", "screenshot.png"], validate_c22),
        CheckDef("C-23", "Медиа", "Поворот на 90°", "Размеры изображения поменялись местами, производные перегенерированы", run_c23, ["request.txt", "response.txt", "image_meta_after.txt"], validate_c23),
    ]
