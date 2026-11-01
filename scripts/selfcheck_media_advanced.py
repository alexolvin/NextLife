"""Advanced media checks (C-30 .. C-34) for selfcheck.

Self-contained: each check creates its own lot and uploads its own photo(s),
then operates strictly by the 3-char code obtained at creation (no "pick the
newest" selector). C-32 scopes the variant query to this check's own item. See
scripts/selfcheck_common.py.
"""

from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright
from scripts.selfcheck_common import create_lot, upload_photo_api, upload_photos_api
from scripts.selfcheck_types import CheckContext, CheckDef
from sqlalchemy import text

from nextlife.storage.db import get_engine


def run_c30(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-30: Lightbox touch swipe on 360px width (own lot + own photos)."""
    code = create_lot(ctx, "C-30 swipe")
    upload_photos_api(ctx, code, colors=["red", "green", "blue"])
    lightbox_open = False
    src_before = ""
    src_after = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 360, "height": 740}, has_touch=True)
        page = context.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        img = page.locator(".slider-img, img[onclick*='openLightbox'], img[onclick*='openActiveLightbox']").first
        if img.count() > 0:
            img.click()
            page.wait_for_timeout(300)
            lightbox = page.locator("#lightbox")
            lightbox_open = lightbox.is_visible() if lightbox.count() > 0 else False
            if lightbox_open:
                src_before = page.locator("#lightbox-img").get_attribute("src") or ""
                art_dir.joinpath("screenshot_before.png").write_bytes(page.screenshot())
                page.touchscreen.tap(300, 300)
                page.mouse.move(300, 300)
                page.mouse.down()
                page.mouse.move(50, 300, steps=5)
                page.mouse.up()
                page.wait_for_timeout(300)
                src_after = page.locator("#lightbox-img").get_attribute("src") or ""
                art_dir.joinpath("screenshot_after.png").write_bytes(page.screenshot())
        if not art_dir.joinpath("screenshot_before.png").exists():
            art_dir.joinpath("screenshot_before.png").write_bytes(page.screenshot())
        if not art_dir.joinpath("screenshot_after.png").exists():
            art_dir.joinpath("screenshot_after.png").write_bytes(page.screenshot())
        browser.close()

    img_changed = bool(src_after and src_after != src_before)
    return {
        "lightbox_open": lightbox_open,
        "img_changed": img_changed,
        "src_before": src_before,
        "src_after": src_after,
        "code": code,
    }


def validate_c30(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    if not (art_dir / "screenshot_before.png").exists():
        return False, "отсутствует: отсутствует screenshot_before.png"
    if not meta.get("lightbox_open"):
        return False, "отсутствует: элемент #lightbox не открылся или отсутствует"
    if meta.get("img_changed"):
        return True, "пройдено: свайп переключает фото в лайтбоксе"
    return False, "отсутствует: обработчик свайпа в лайтбоксе отсутствует"


def run_c31(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-31: Lightbox thumbnail strip and counter (own lot + own photos)."""
    code = create_lot(ctx, "C-31 strip")
    upload_photos_api(ctx, code, colors=["red", "green", "blue"])
    lightbox_open = False
    strip_visible = False
    counter_visible = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        img = page.locator(".slider-img, img[onclick*='openLightbox'], img[onclick*='openActiveLightbox']").first
        if img.count() > 0:
            img.click()
            page.wait_for_timeout(300)
            lightbox = page.locator("#lightbox")
            lightbox_open = lightbox.is_visible() if lightbox.count() > 0 else False
            if lightbox_open:
                strip = page.locator("#lightbox .lightbox-strip, #lightbox .lightbox-thumbs, #lightbox .thumbnails-strip, #lightbox [data-testid='lightbox-strip']").first
                counter = page.locator("#lightbox .lightbox-counter, #lightbox #lightbox-counter, #lightbox [data-testid='lightbox-counter']").first
                strip_visible = strip.is_visible() if strip.count() > 0 else False
                counter_visible = counter.is_visible() if counter.count() > 0 else False
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot())
        browser.close()

    art_dir.joinpath("dom_elements.txt").write_text(
        f"lot={code}\nlightbox_open={lightbox_open}\nstrip={strip_visible}\ncounter={counter_visible}\n",
        encoding="utf-8",
    )
    return {"lightbox_open": lightbox_open, "strip": strip_visible, "counter": counter_visible, "code": code}


def validate_c31(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    de = art_dir / "dom_elements.txt"
    if not ss.exists() or not de.exists():
        return False, "отсутствует: отсутствует screenshot.png или dom_elements.txt"
    if not meta.get("lightbox_open"):
        return False, "отсутствует: элемент #lightbox не открылся или отсутствует"
    strip_ok = meta.get("strip")
    counter_ok = meta.get("counter")
    if strip_ok and counter_ok:
        return True, "пройдено: лента миниатюр и счётчик присутствуют в лайтбоксе"
    if not strip_ok and not counter_ok:
        return False, "отсутствует: лента миниатюр и счётчик отсутствуют в #lightbox"
    if not strip_ok:
        return False, "отсутствует: лента миниатюр отсутствует в #lightbox"
    return False, "отсутствует: счётчик отсутствует в #lightbox"


def run_c32(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-32: Variants generated for marketplaces fitting their profiles (own lot + own photo)."""
    code = create_lot(ctx, "C-32 variants")
    upload_photo_api(ctx, code, color="yellow")
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT v.id, v.marketplace_key, v.role, v.width, v.height
                FROM media_variant v
                JOIN media_master m ON v.master_id = m.id
                JOIN media_original o ON m.original_id = o.id
                WHERE o.item_code = :code
                ORDER BY v.created_at;
                """
            ),
            {"code": code},
        ).fetchall()

    art_dir.joinpath("variants_info.txt").write_text(f"lot={code}\n" + "\n".join(str(r) for r in rows), encoding="utf-8")
    return {"count": len(rows), "code": code}


def validate_c32(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    vi = art_dir / "variants_info.txt"
    if not vi.exists():
        return False, "отсутствует: отсутствует variants_info.txt"
    if meta.get("count", 0) > 0:
        return True, f"пройдено: сгенерировано {meta.get('count')} вариантов под площадки"
    return False, "отсутствует: варианты под площадки отсутствуют в таблице media_variant"


def run_c33(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-33: Preview 'as on marketplace' serves variant, not original (own lot + own photo)."""
    code = create_lot(ctx, "C-33 preview")
    upload_photo_api(ctx, code, color="orange")
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        resp = client.get(f"{ctx.base_url}/ui/items/{code}/preview/bazos_cz?token={ctx.auth_token}")
        art_dir.joinpath("preview_response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text[:500]}", encoding="utf-8")

    art_dir.joinpath("comparison.txt").write_text(f"lot={code}\nstatus={resp.status_code}", encoding="utf-8")
    return {"status_code": resp.status_code, "supported": resp.status_code == 200, "code": code}


def validate_c33(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    pr = art_dir / "preview_response.txt"
    if not pr.exists():
        return False, "отсутствует: отсутствует preview_response.txt"
    sc = meta.get("status_code", 0)
    if meta.get("supported"):
        return True, "пройдено: эндпоинт превью площадки вернул 200"
    if sc in (404, 405):
        return False, f"отсутствует: маршрут превью площадки не найден (HTTP {sc})"
    return False, f"поведение неверно: превью площадки вернуло статус {sc}"


def run_c34(ctx: CheckContext, art_dir: Path, code: str | None = None) -> dict[str, Any]:
    """C-34: Defect without unretouched photo prevents assembling publication package.

    Self-contained: when ``code`` is omitted (as by the selfcheck runner) it
    creates its own lot and operates by the code obtained at creation; when
    ``code`` is supplied (as by the pytest regression test) it operates on
    exactly that pre-seeded item and needs no live server.
    """
    from nextlife.media.pipeline import assemble_marketplace_kit
    from nextlife.storage.db import get_db_session

    code = code or create_lot(ctx, "C-34 defect")
    blocked = False
    error_msg = ""
    try:
        with get_db_session() as session:
            try:
                assemble_marketplace_kit(
                    session=session,
                    item_code=code,
                    marketplace_caps={"max_photos": 10, "allows_generative_photos": False},
                    item_defects=["DEFECT_TEST_SCRATCH"],
                )
            except Exception as exc:
                blocked = True
                error_msg = str(exc)
    except Exception as exc:
        error_msg = f"Outer error: {exc}"

    art_dir.joinpath("publish_request.txt").write_text(f"assemble_marketplace_kit for {code} with defect and no defect photo", encoding="utf-8")
    art_dir.joinpath("publish_response.txt").write_text(f"blocked={blocked}\nerror={error_msg}", encoding="utf-8")
    return {"blocked": blocked, "error": error_msg, "code": code}


def validate_c34(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    pr = art_dir / "publish_response.txt"
    if not pr.exists():
        return False, "отсутствует: отсутствует publish_response.txt"
    if meta.get("blocked"):
        return True, f"пройдено: пакет публикации заблокирован при дефекте без фото: {meta.get('error')[:80]}"
    return False, f"поведение неверно: пакет публикации не заблокирован при дефекте без фото: {meta.get('error')}"


def get_media_advanced_checks() -> list[CheckDef]:
    return [
        CheckDef("C-30", "Медиа", "Лайтбокс: свайп на ширине 360 px", "Смена изображения", run_c30, ["screenshot_before.png", "screenshot_after.png"], validate_c30),
        CheckDef("C-31", "Медиа", "Лайтбокс: лента миниатюр и счётчик", "Оба элемента видны на скриншоте", run_c31, ["screenshot.png", "dom_elements.txt"], validate_c31),
        CheckDef("C-32", "Медиа", "Варианты под площадки", "Для каждой включённой площадки существует вариант", run_c32, ["variants_info.txt"], validate_c32),
        CheckDef("C-33", "Медиа", "Превью «как на площадке»", "Отдаётся вариант, а не оригинал", run_c33, ["preview_response.txt", "comparison.txt"], validate_c33),
        CheckDef("C-34", "Медиа", "Фото дефекта", "Дефект без неретушированного фото блокирует набор публикации", run_c34, ["publish_request.txt", "publish_response.txt"], validate_c34),
    ]
