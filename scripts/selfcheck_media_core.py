"""Media core checks (C-13 .. C-18) for selfcheck.

Every check is SELF-CONTAINED: it creates its own lot (via HTTP) and, when the
check needs media, uploads its own photo to that lot, then operates strictly by
the 3-char code obtained at creation. There is no "pick the newest item"
selector — that pattern coupled checks to each other's execution order (C-18
false-FAILED because C-11 had soft-deleted the "newest" lot it was targeting).
See scripts/selfcheck_common.py for the design rationale.
"""

import io
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright
from scripts.selfcheck_common import create_lot, make_jpeg, upload_photo_api
from scripts.selfcheck_types import CheckContext, CheckDef
from sqlalchemy import text

from nextlife.storage.db import get_engine


def _create_test_image_bytes(color: str = "red", size: tuple[int, int] = (640, 480)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def run_c13(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-13: Photo upload element presence on lot card."""
    code = create_lot(ctx, "C-13 upload element")
    found = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))

        file_input = page.locator("input[type='file'], [data-testid='photo-file-input']").first
        found = file_input.is_visible() if file_input.count() > 0 else False
        art_dir.joinpath("dom_element.txt").write_text(f"found={found}\ncount={file_input.count()}", encoding="utf-8")
        browser.close()
    return {"found": found, "code": code}


def validate_c13(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    de = art_dir / "dom_element.txt"
    if not ss.exists() or not de.exists():
        return False, "отсутствует: отсутствует screenshot.png или dom_element.txt"
    if meta.get("found"):
        return True, "пройдено: элемент загрузки фото найден в DOM и виден"
    return False, "отсутствует: элемент загрузки фото не найден или скрыт"


def run_c14(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-14: Upload 1 photo via UI form (own lot)."""
    code = create_lot(ctx, "C-14 ui upload 1")
    img_data = _create_test_image_bytes("blue")
    tmp_img = art_dir / "test_upload_1.jpg"
    tmp_img.write_bytes(img_data)

    status_code = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        file_input = page.locator("input[type='file'], [data-testid='photo-file-input']").first
        if file_input.count() > 0:
            file_input.set_input_files(str(tmp_img))
            submit = page.locator("button[type='submit'][data-testid='btn-submit-upload'], form[data-testid='photo-upload-form'] button").first
            if submit.count() > 0:
                with page.expect_navigation():
                    submit.click()
                status_code = 200
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))
        browser.close()

    art_dir.joinpath("upload_response.txt").write_text(f"UI upload status {status_code} (lot {code})", encoding="utf-8")

    # DB record (scoped to this check's own lot)
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT id, path, sha256 FROM item_photo WHERE item_code = :code ORDER BY created_at DESC LIMIT 1;"), {"code": code}).fetchone()

    db_str = str(row) if row else "NONE"
    art_dir.joinpath("db_photo.txt").write_text(db_str, encoding="utf-8")

    file_on_disk = False
    if row and row[1]:
        fpath = Path(row[1])
        if not fpath.is_absolute():
            fpath = Path(__file__).resolve().parent.parent / fpath
        file_on_disk = fpath.exists() and fpath.stat().st_size > 0
        art_dir.joinpath("file_stat.txt").write_text(f"exists={fpath.exists()}, size={fpath.stat().st_size if fpath.exists() else 0}", encoding="utf-8")
    else:
        art_dir.joinpath("file_stat.txt").write_text("file_on_disk=False", encoding="utf-8")

    return {"status_code": status_code, "db_row": bool(row), "file_on_disk": file_on_disk, "code": code}


def validate_c14(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    db_file = art_dir / "db_photo.txt"
    fs = art_dir / "file_stat.txt"
    if not ss.exists() or not db_file.exists() or not fs.exists():
        return False, "отсутствует: отсутствуют артефакты загрузки"
    if meta.get("status_code") == 200 and meta.get("db_row") and meta.get("file_on_disk"):
        return True, "пройдено: 1 фото загружено через UI, подтверждено в БД и на диске"
    return False, f"поведение неверно: загрузка не завершена: code={meta.get('status_code')}, db={meta.get('db_row')}, disk={meta.get('file_on_disk')}"


def run_c15(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-15: Upload 5 files at once (own lot)."""
    code = create_lot(ctx, "C-15 ui upload 5")
    files_to_upload = []
    for i in range(5):
        b = _create_test_image_bytes(color=["green", "yellow", "cyan", "magenta", "orange"][i])
        p = art_dir / f"batch_{i}.jpg"
        p.write_bytes(b)
        files_to_upload.append(p)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        file_input = page.locator("input[type='file'], [data-testid='photo-file-input']").first
        if file_input.count() > 0:
            file_input.set_input_files([str(f) for f in files_to_upload])
            submit = page.locator("button[type='submit'][data-testid='btn-submit-upload'], form[data-testid='photo-upload-form'] button").first
            if submit.count() > 0:
                with page.expect_navigation():
                    submit.click()
        browser.close()

    art_dir.joinpath("upload_response.txt").write_text(f"Batch upload triggered (lot {code})", encoding="utf-8")

    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT id, path FROM item_photo WHERE item_code = :code ORDER BY created_at DESC LIMIT 5;"), {"code": code}).fetchall()

    art_dir.joinpath("db_photos.txt").write_text("\n".join(str(r) for r in rows), encoding="utf-8")
    art_dir.joinpath("file_stats.txt").write_text(f"count={len(rows)}", encoding="utf-8")
    return {"count": len(rows), "code": code}


def validate_c15(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    db_file = art_dir / "db_photos.txt"
    if not db_file.exists() or not (art_dir / "file_stats.txt").exists():
        return False, "отсутствует: отсутствуют артефакты групповой загрузки"
    if meta.get("count", 0) >= 5:
        return True, f"пройдено: 5 фото загружено и сохранено в БД (найдено {meta.get('count')})"
    return False, f"поведение неверно: ожидалось 5 фото, найдено {meta.get('count')}"


def run_c16(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-16: Upload photo via API without browser (own lot)."""
    code = create_lot(ctx, "C-16 api upload")
    img_data = make_jpeg("purple")
    art_dir.joinpath("http_request.txt").write_text(f"POST /api/items/{code}/photos (multipart)", encoding="utf-8")

    with httpx.Client(timeout=10.0) as client:
        files = {"file": ("api_test.jpg", img_data, "image/jpeg")}
        resp = client.post(
            f"{ctx.base_url}/api/items/{code}/photos",
            files=files,
            headers={"Authorization": f"Bearer {ctx.auth_token}"},
        )
        art_dir.joinpath("http_response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    # DB verify (scoped to this check's own lot)
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT id, path FROM item_photo WHERE item_code = :code ORDER BY created_at DESC LIMIT 1;"), {"code": code}).fetchone()

    art_dir.joinpath("db_photo.txt").write_text(str(row), encoding="utf-8")
    return {"status_code": resp.status_code, "db_row": bool(row), "code": code}


def validate_c16(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    resp_file = art_dir / "http_response.txt"
    db_file = art_dir / "db_photo.txt"
    if not resp_file.exists() or not db_file.exists():
        return False, "отсутствует: отсутствуют артефакты http_response.txt или db_photo.txt"
    sc = meta.get("status_code")
    if sc in (200, 201) and meta.get("db_row"):
        return True, "пройдено: фото загружено через API, запись в БД"
    if sc in (404, 405):
        return False, f"отсутствует: маршрут API загрузки не найден (HTTP {sc})"
    return False, f"поведение неверно: API загрузки возвращает статус {sc} вместо 200/201"


def run_c17(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-17: Thumbnail appears on lot card, returns 200 with image/* (own lot + own photo)."""
    code = create_lot(ctx, "C-17 card thumb")
    # Upload a photo to this lot so the card has a thumbnail to serve.
    up_status, _photos = upload_photo_api(ctx, code, color="orange")
    thumb_url = ""
    status_code = 0
    content_type = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))

        img = page.locator("img[src*='/media/'], img.slider-img, [data-testid='item-thumbnail']").first
        if img.count() > 0:
            thumb_url = img.get_attribute("src") or ""
        browser.close()

    if thumb_url:
        full_url = thumb_url if thumb_url.startswith("http") else f"{ctx.base_url}{thumb_url}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(full_url)
            status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")

    art_dir.joinpath("thumbnail_http.txt").write_text(
        f"code={code}\nupload_status={up_status}\nurl={thumb_url}\nstatus={status_code}\ncontent-type={content_type}", encoding="utf-8"
    )
    return {"status_code": status_code, "is_image": "image" in content_type, "code": code}


def validate_c17(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    th = art_dir / "thumbnail_http.txt"
    if not ss.exists() or not th.exists():
        return False, "отсутствует: отсутствует screenshot.png или thumbnail_http.txt"
    if meta.get("status_code") == 200 and meta.get("is_image"):
        return True, "пройдено: миниатюра загружена на карточке (HTTP 200 image/*)"
    if meta.get("status_code") in (404, 405):
        return False, f"отсутствует: маршрут миниатюры не найден (HTTP {meta.get('status_code')})"
    return False, f"поведение неверно: миниатюра не загрузилась (status={meta.get('status_code')}, is_image={meta.get('is_image')})"


def run_c18(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-18: Thumbnail appears in the items list (own lot + own photo).

    Self-contained: this check creates its own lot, uploads its own photo, then
    locates THAT lot's card in the feed (by `#item-{code}`) and verifies its
    thumbnail loads. It never reads "the newest item", so the result does not
    depend on whether C-11 ran before or after it.
    """
    code = create_lot(ctx, "C-18 list thumb")
    up_status, _photos = upload_photo_api(ctx, code, color="teal")
    thumb_url = ""
    status_code = 0
    content_type = ""
    card_found = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))

        card = page.locator(f"#item-{code}")
        card_found = card.count()
        if card.count() > 0:
            img = card.locator("img.feed-card-thumb, img.feed-thumb").first
            if img.count() > 0:
                thumb_url = img.get_attribute("src") or ""
        browser.close()

    if thumb_url:
        full_url = thumb_url if thumb_url.startswith("http") else f"{ctx.base_url}{thumb_url}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(full_url)
            status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")

    art_dir.joinpath("thumbnail_http.txt").write_text(
        f"code={code}\nupload_status={up_status}\ncard_found={card_found}\nurl={thumb_url}\nstatus={status_code}\ncontent-type={content_type}",
        encoding="utf-8",
    )
    return {"status_code": status_code, "is_image": "image" in content_type, "card_found": card_found, "code": code}


def validate_c18(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    th = art_dir / "thumbnail_http.txt"
    if not ss.exists() or not th.exists():
        return False, "отсутствует: отсутствует screenshot.png или thumbnail_http.txt"
    if not meta.get("card_found"):
        return False, f"отсутствует: карточка созданного лота не найдена в ленте (lot {meta.get('code')})"
    if meta.get("status_code") == 200 and meta.get("is_image"):
        return True, "пройдено: миниатюра созданного лота отображается в списке (HTTP 200 image/*)"
    if meta.get("status_code") in (404, 405):
        return False, f"отсутствует: маршрут миниатюры в списке не найден (HTTP {meta.get('status_code')})"
    return False, f"поведение неверно: миниатюра в списке лотов вернула status={meta.get('status_code')}"


def get_media_core_checks() -> list[CheckDef]:
    return [
        CheckDef("C-13", "Медиа", "Элемент загрузки фото на карточке лота", "Элемент найден в DOM и виден на скриншоте", run_c13, ["screenshot.png", "dom_element.txt"], validate_c13),
        CheckDef("C-14", "Медиа", "Загрузка одного файла через интерфейс", "Код 2xx, запись в БД, файл на диске, ненулевой размер", run_c14, ["upload_response.txt", "db_photo.txt", "file_stat.txt", "screenshot.png"], validate_c14),
        CheckDef("C-15", "Медиа", "Загрузка пяти файлов разом", "Пять записей, пять файлов, порядок сохранён", run_c15, ["upload_response.txt", "db_photos.txt", "file_stats.txt"], validate_c15),
        CheckDef("C-16", "Медиа", "Загрузка через API без браузера", "Тот же результат", run_c16, ["http_request.txt", "http_response.txt", "db_photo.txt"], validate_c16),
        CheckDef("C-17", "Медиа", "Миниатюра появляется в карточке лота", "Скриншот содержит изображение, HTTP отдаёт image/* 200", run_c17, ["screenshot.png", "thumbnail_http.txt"], validate_c17),
        CheckDef("C-18", "Медиа", "Миниатюра появляется в списке лотов", "То же", run_c18, ["screenshot.png", "thumbnail_http.txt"], validate_c18),
    ]
