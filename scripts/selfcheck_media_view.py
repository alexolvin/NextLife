"""Media viewer, crop, integrity, and variants checks (C-24 .. C-29) for selfcheck.

Self-contained: each check creates its own lot and uploads its own photo(s),
then operates strictly by the 3-char code obtained at creation (no "pick the
newest" selector). The lightbox checks upload >=2 photos so next/prev/arrow
navigation actually changes the displayed image. See scripts/selfcheck_common.py.
"""

import hashlib
import io
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright
from scripts.selfcheck_common import create_lot, upload_photo_api, upload_photos_api
from scripts.selfcheck_types import CheckContext, CheckDef
from sqlalchemy import text

from nextlife.storage.db import get_engine


def run_c24(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-24: Crop image by coordinate bounding box (own lot + own photo)."""
    code = create_lot(ctx, "C-24 crop")
    upload_photo_api(ctx, code, color="red")
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/api/items/{code}/photos/crop",
            json={"box": [10, 10, 200, 200]},
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("request.txt").write_text(f"POST /api/items/{code}/photos/crop [10, 10, 200, 200]", encoding="utf-8")
        art_dir.joinpath("response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    art_dir.joinpath("image_meta.txt").write_text(f"crop_status={resp.status_code} (lot {code})", encoding="utf-8")
    return {"status_code": resp.status_code, "supported": resp.status_code == 200, "code": code}


def validate_c24(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    rf = art_dir / "response.txt"
    if not rf.exists():
        return False, "отсутствует: отсутствует response.txt"
    sc = meta.get("status_code", 0)
    if meta.get("supported"):
        return True, "пройдено: кроп изображения выполнен"
    if sc in (404, 405):
        return False, f"отсутствует: эндпоинт кропа фото не найден (HTTP {sc})"
    return False, f"поведение неверно: эндпоинт кропа фото вернул статус {sc}"


def run_c25(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-25: Original image remains unchanged (sha256 matches recorded upload sha256) — own lot + own original."""
    code = create_lot(ctx, "C-25 original sha")
    upload_photo_api(ctx, code, color="blue")
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT path, sha256 FROM item_photo WHERE item_code = :code AND role = 'ORIGINAL' AND sha256 IS NOT NULL LIMIT 1;"),
            {"code": code},
        ).fetchone()

    if not row:
        art_dir.joinpath("sha256_check.txt").write_text("NO_ORIGINAL_FOUND", encoding="utf-8")
        return {"match": False, "code": code}

    fpath, recorded_sha = Path(row[0]), row[1]
    if not fpath.is_absolute():
        fpath = Path(__file__).resolve().parent.parent / fpath

    actual_sha = ""
    if fpath.exists():
        h = hashlib.sha256()
        h.update(fpath.read_bytes())
        actual_sha = h.hexdigest()

    match = bool(actual_sha and actual_sha == recorded_sha)
    art_dir.joinpath("sha256_check.txt").write_text(
        f"lot={code}\nfile={fpath}\nrecorded={recorded_sha}\nactual={actual_sha}\nmatch={match}", encoding="utf-8"
    )
    return {"match": match, "actual": actual_sha, "recorded": recorded_sha, "code": code}


def validate_c25(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    sc = art_dir / "sha256_check.txt"
    if not sc.exists():
        return False, "отсутствует: отсутствует sha256_check.txt"
    if meta.get("match"):
        return True, "пройдено: sha256 оригинала совпадает с записанным"
    return False, f"поведение неверно: несовпадение хэша оригинала: {meta.get('actual')} vs {meta.get('recorded')}"


def run_c26(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-26: EXIF and GPS stripped from served image (own lot + own photo)."""
    code = create_lot(ctx, "C-26 exif")
    upload_photo_api(ctx, code, color="purple")
    served_bytes = b""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        img = page.locator("img[src*='/media/']").first
        if img.count() > 0:
            src = img.get_attribute("src") or ""
            full_url = src if src.startswith("http") else f"{ctx.base_url}{src}"
            with httpx.Client(timeout=10.0) as client:
                r = client.get(full_url)
                if r.status_code == 200:
                    served_bytes = r.content
        browser.close()

    has_exif = False
    has_gps = False
    if served_bytes:
        try:
            pil_img = Image.open(io.BytesIO(served_bytes))
            exif = pil_img.getexif()
            has_exif = bool(exif)
            has_gps = 34853 in exif  # GPSInfo tag ID
        except Exception:
            pass

    art_dir.joinpath("exif_dump.txt").write_text(
        f"lot={code}\nhas_exif={has_exif}, has_gps={has_gps}, bytes_len={len(served_bytes)}", encoding="utf-8"
    )
    return {"served": bool(served_bytes), "clean": not has_exif and not has_gps, "code": code}


def validate_c26(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ed = art_dir / "exif_dump.txt"
    if not ed.exists():
        return False, "отсутствует: отсутствует exif_dump.txt"
    if meta.get("served") and meta.get("clean"):
        return True, "пройдено: отдаваемое изображение очищено от EXIF/GPS"
    if not meta.get("served"):
        return False, "отсутствует: отдаваемое изображение не найдено"
    return False, "поведение неверно: обнаружены метаданные EXIF или GPS"


def run_c27(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-27: Lightbox next photo button (own lot + own photos)."""
    code = create_lot(ctx, "C-27 lightbox next")
    upload_photos_api(ctx, code, colors=["red", "green", "blue"])
    lightbox_open = False
    btn_found = False
    src_before = ""
    src_after = ""
    counter_before = ""
    counter_after = ""

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
                src_before = page.locator("#lightbox-img").get_attribute("src") or ""
                c_el = page.locator("#lightbox .lightbox-counter, #lightbox .slider-counter")
                counter_before = c_el.inner_text() if c_el.count() > 0 else ""
                art_dir.joinpath("screenshot_before.png").write_bytes(page.screenshot())
                next_btn = page.locator("#lightbox-next, #lightbox .slider-next, #lightbox button.next").first
                btn_found = next_btn.is_visible() if next_btn.count() > 0 else False
                if btn_found:
                    next_btn.click()
                    page.wait_for_timeout(300)
                    src_after = page.locator("#lightbox-img").get_attribute("src") or ""
                    c_el2 = page.locator("#lightbox .lightbox-counter, #lightbox .slider-counter")
                    counter_after = c_el2.inner_text() if c_el2.count() > 0 else ""
                art_dir.joinpath("screenshot_after.png").write_bytes(page.screenshot())
        if not art_dir.joinpath("screenshot_before.png").exists():
            art_dir.joinpath("screenshot_before.png").write_bytes(page.screenshot())
        if not art_dir.joinpath("screenshot_after.png").exists():
            art_dir.joinpath("screenshot_after.png").write_bytes(page.screenshot())
        browser.close()

    img_changed = bool(src_after and src_after != src_before)
    art_dir.joinpath("counter_text.txt").write_text(
        f"lot={code}\nlightbox_open={lightbox_open}\nbtn_found={btn_found}\nsrc_before={src_before}\nsrc_after={src_after}\ncounter_before={counter_before}\ncounter_after={counter_after}\nimg_changed={img_changed}\n",
        encoding="utf-8",
    )
    return {
        "lightbox_open": lightbox_open,
        "btn_found": btn_found,
        "img_changed": img_changed,
        "src_before": src_before,
        "src_after": src_after,
        "code": code,
    }


def validate_c27(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    if not (art_dir / "counter_text.txt").exists():
        return False, "отсутствует: отсутствует counter_text.txt"
    if not meta.get("lightbox_open"):
        return False, "отсутствует: элемент #lightbox не открылся или отсутствует"
    if not meta.get("btn_found"):
        return False, "отсутствует: кнопка следующего фото в лайтбоксе отсутствует"
    if meta.get("img_changed"):
        return True, "пройдено: следующее фото в лайтбоксе переключено кнопкой"
    return False, "поведение неверно: кнопка нажата, но изображение не сменилось"


def run_c28(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-28: Lightbox prev photo button (own lot + own photos)."""
    code = create_lot(ctx, "C-28 lightbox prev")
    upload_photos_api(ctx, code, colors=["red", "green", "blue"])
    lightbox_open = False
    btn_found = False
    src_before = ""
    src_after = ""

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
                next_btn = page.locator("#lightbox-next, #lightbox .slider-next").first
                if next_btn.count() > 0 and next_btn.is_visible():
                    next_btn.click()
                    page.wait_for_timeout(300)
                src_before = page.locator("#lightbox-img").get_attribute("src") or ""
                art_dir.joinpath("screenshot_before.png").write_bytes(page.screenshot())
                prev_btn = page.locator("#lightbox-prev, #lightbox .slider-prev, #lightbox button.prev").first
                btn_found = prev_btn.is_visible() if prev_btn.count() > 0 else False
                if btn_found:
                    prev_btn.click()
                    page.wait_for_timeout(300)
                    src_after = page.locator("#lightbox-img").get_attribute("src") or ""
                art_dir.joinpath("screenshot_after.png").write_bytes(page.screenshot())
        if not art_dir.joinpath("screenshot_before.png").exists():
            art_dir.joinpath("screenshot_before.png").write_bytes(page.screenshot())
        if not art_dir.joinpath("screenshot_after.png").exists():
            art_dir.joinpath("screenshot_after.png").write_bytes(page.screenshot())
        browser.close()

    img_changed = bool(src_after and src_after != src_before)
    art_dir.joinpath("counter_text.txt").write_text(
        f"lot={code}\nlightbox_open={lightbox_open}\nbtn_found={btn_found}\nsrc_before={src_before}\nsrc_after={src_after}\nimg_changed={img_changed}\n",
        encoding="utf-8",
    )
    return {
        "lightbox_open": lightbox_open,
        "btn_found": btn_found,
        "img_changed": img_changed,
        "src_before": src_before,
        "src_after": src_after,
        "code": code,
    }


def validate_c28(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    if not (art_dir / "counter_text.txt").exists():
        return False, "отсутствует: отсутствует counter_text.txt"
    if not meta.get("lightbox_open"):
        return False, "отсутствует: элемент #lightbox не открылся или отсутствует"
    if not meta.get("btn_found"):
        return False, "отсутствует: кнопка предыдущего фото в лайтбоксе отсутствует"
    if meta.get("img_changed"):
        return True, "пройдено: предыдущее фото в лайтбоксе переключено кнопкой"
    return False, "поведение неверно: кнопка назад нажата, но изображение не сменилось"


def run_c29(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-29: Lightbox keyboard arrow navigation (own lot + own photos)."""
    code = create_lot(ctx, "C-29 lightbox arrow")
    upload_photos_api(ctx, code, colors=["red", "green", "blue"])
    lightbox_open = False
    src_before = ""
    src_after = ""

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
                src_before = page.locator("#lightbox-img").get_attribute("src") or ""
                art_dir.joinpath("screenshot_before.png").write_bytes(page.screenshot())
                page.keyboard.press("ArrowRight")
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


def validate_c29(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    if not (art_dir / "screenshot_before.png").exists():
        return False, "отсутствует: отсутствует screenshot_before.png"
    if not meta.get("lightbox_open"):
        return False, "отсутствует: элемент #lightbox не открылся или отсутствует"
    if meta.get("img_changed"):
        return True, "пройдено: стрелки клавиатуры переключают фото в лайтбоксе"
    return False, "отсутствует: обработчик стрелок клавиатуры в лайтбоксе отсутствует"


def get_media_view_checks() -> list[CheckDef]:
    return [
        CheckDef("C-24", "Медиа", "Кроп по рамке", "Результат соответствует рамке, оригинал не изменён", run_c24, ["request.txt", "response.txt", "image_meta.txt"], validate_c24),
        CheckDef("C-25", "Медиа", "Оригинал неизменен после всех операций", "sha256 оригинала совпадает с записанным при загрузке", run_c25, ["sha256_check.txt"], validate_c25),
        CheckDef("C-26", "Медиа", "EXIF в отдаваемом файле", "EXIF отсутствует, GPS отсутствует", run_c26, ["exif_dump.txt"], validate_c26),
        CheckDef("C-27", "Медиа", "Лайтбокс: следующее фото кнопкой", "Номер в счётчике увеличился, изображение сменилось", run_c27, ["screenshot_before.png", "screenshot_after.png", "counter_text.txt"], validate_c27),
        CheckDef("C-28", "Медиа", "Лайтбокс: предыдущее фото кнопкой", "То же в обратную сторону", run_c28, ["screenshot_before.png", "screenshot_after.png", "counter_text.txt"], validate_c28),
        CheckDef("C-29", "Медиа", "Лайтбокс: стрелки клавиатуры", "Смена изображения", run_c29, ["screenshot_before.png", "screenshot_after.png"], validate_c29),
    ]
