#!/usr/bin/env python3
"""N5. Запуск по состояниям экранов (таблица состояний ТЗ, раздел 8).

Для каждого состояния:
  (если мутация) бэкап затронутых таблиц на диск -> мутация (commit) ->
  прогон прибора (ui_audit.run, список экранов заменён на список состояния) ->
  восстановление из бэкапа -> ПРОВЕРКА восстановления (хэш строк).

Виды состояний:
  instrument — экраны прогоняются прибором (build_screens заменяется).
  custom     — Playwright-функция (лайтбокс-позиции, валидация, модалка, wizard-resume).

Безопасность: мутации применяются к DEV-БД только с проверенным восстановлением.
Если восстановление не сходится по хэшу — состояние помечается ERROR
(RESTORE FAILED) и успех не утверждается. Бэкап — <out>/db_backup.json.

Запуск:
  python3 scripts/ui_audit_states.py --all
  python3 scripts/ui_audit_states.py --state items-empty
  python3 scripts/ui_audit_states.py --list
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
_lib = ROOT / "lib" / "playwright_libs"
if _lib.exists():
    os.environ["LD_LIBRARY_PATH"] = f"{_lib}:{os.environ.get('LD_LIBRARY_PATH','')}".strip(":")

from psycopg2.extras import Json  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from nextlife.storage.db import get_engine  # noqa: E402

import ui_audit  # noqa: E402

BASE = os.environ.get("NEXTLIFE_BASE_URL", "http://localhost:8081")
TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
DSN = os.environ.get("NEXTLIFE_DSN", "")
VPS = ["360", "390", "768", "1280"]


# ---------------------------------------------------------------- DB backup
# Raw psycopg2 checkouts from the app engine's pool (id(conn) -> pool proxy),
# released via _release() so the pool is never exhausted across the many state
# runs of this tool.
_raw_proxies: dict[int, object] = {}


def _connect():
    if not DSN:
        raise SystemExit("NEXTLIFE_DSN not set")
    # Single DSN source: check out a raw psycopg2 connection from the app
    # engine's pool (no direct DBAPI connect call, no driver-suffix stripping).
    # This tool needs low-level cursor/executemany/description/Json access,
    # so it operates on the raw DBAPI connection the engine hands back.
    proxy = get_engine().raw_connection()
    conn = proxy.driver_connection
    _raw_proxies[id(conn)] = proxy
    conn.autocommit = False
    return conn


def _release(conn):
    proxy = _raw_proxies.pop(id(conn), None)
    if proxy is not None:
        proxy.close()
    else:
        conn.close()


def _ser(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bytes):
        return v.hex()
    return v


def _table_hash(cols, rows):
    data = [[_ser(x) for x in r] for r in rows]
    data.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _snapshot(conn, tables):
    cur = conn.cursor()
    snap = {}
    for t in tables:
        cur.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        snap[t] = (cols, rows, _table_hash(cols, rows))
    return snap


# Порядок восстановления: родители раньше детей (FK, проверено по information_schema).
# handover/settlement внешние дети deal — включены для FK-закрытости полного сброса.
RESTORE_ORDER = ["item", "lot", "listing", "approval", "delivery_option", "item_defect",
                 "lot_pricing", "price_history", "research_run", "research_result",
                 "media_original", "media_master", "media_variant", "item_photo",
                 "deal", "handover", "settlement", "payment_option", "thread", "message"]


def _adapt(v):
    """psycopg2 не адаптирует dict/list в jsonb сам — оборачиваем явно."""
    if isinstance(v, (dict, list)):
        return Json(v)
    return v


# Самоссылающиеся FK: вставленная строка не может ссылаться на ещё не вставленную.
# item_photo.derived_from_id -> item_photo.id (производные фото ссылаются на источник).
_SELF_FK = {"item_photo": "derived_from_id"}


def _topo_order(rows, cols, ref_col):
    """Топологический порядок по самоссылке: строка вставима, когда её
    источник уже выдан (или ссылается на строку вне батча).

    Порядок ВХОДЯЩЕГО списка не имеет значения (heap-порядок БД меняется
    после UPDATE/VACUUM и не гарантирует «родитель раньше ребёнка»).
    Цикл/обрыв — fail-closed: RuntimeError вместо тихого FK-срыва.
    """
    id_i, ref_i = cols.index("id"), cols.index(ref_col)
    remaining, ordered, emitted = list(rows), [], set()
    while remaining:
        batch_ids = {str(r[id_i]) for r in remaining}
        for i, r in enumerate(remaining):
            ref = r[ref_i]
            # Вставима: корень (None), сирота (родитель вне батча — итог
            # решает FK БД) или источник уже выдан.
            if ref is None or str(ref) not in batch_ids or str(ref) in emitted:
                ordered.append(remaining.pop(i))
                emitted.add(str(r[id_i]))
                break
        else:
            stuck = [str(r[id_i])[:8] for r in remaining]
            raise RuntimeError(
                f"_topo_order: {ref_col} — цикл ссылок внутри батча: {stuck}")
    return ordered


def _restore(conn, snap):
    """Двухфазное восстановление: DELETE детей раньше родителей, INSERT родителей раньше детей."""
    cur = conn.cursor()
    ordered = [t for t in RESTORE_ORDER if t in snap] + [t for t in snap if t not in RESTORE_ORDER]
    for t in reversed(ordered):
        cur.execute(f"DELETE FROM {t}")
    for t in ordered:
        cols, rows, _h = snap[t]
        ref_col = _SELF_FK.get(t)
        if ref_col and ref_col in cols:
            rows = _topo_order(rows, cols, ref_col)
        if rows:
            ph = ",".join(["%s"] * len(cols))
            data = [[_adapt(x) for x in r] for r in rows]
            cur.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({ph})", data)
    conn.commit()


def _verify(conn, snap):
    cur = conn.cursor()
    for t, (cols, rows, h) in snap.items():
        cur.execute(f"SELECT * FROM {t}")
        curcols = [d[0] for d in cur.description]
        currows = cur.fetchall()
        if curcols != cols:
            raise RuntimeError(f"RESTORE FAILED {t}: column mismatch")
        if _table_hash(curcols, currows) != h:
            raise RuntimeError(f"RESTORE FAILED {t}: row hash mismatch")


def _mutate(conn, setup_sql, params_list):
    cur = conn.cursor()
    for sql, params in zip(setup_sql, params_list):
        cur.execute(sql, params or None)
    conn.commit()


# ---------------------------------------------------------------- instrument
def _run_instrument(screens, out_dir):
    original = ui_audit.build_screens
    ui_audit.build_screens = lambda p: screens
    try:
        return ui_audit.run(BASE.rstrip("/"), TOKEN, out_dir, VPS, False, [],
                            no_golden=False, offline=False)
    finally:
        ui_audit.build_screens = original


class RestoreFailedError(RuntimeError):
    """БД может быть в разрушенном/неизвестном состоянии: дальнейший
    прогон состояний ЗАПРЕЩЁН (каждое следующее состояние закрепит и
    «проверит» разрушение — инцидент 2026-09-11: пустая БД, прогон
    «успешно» восстановил пустоту из пустого снапшота)."""


def _snap_from_backup(out_dir):
    """Снапшот из db_backup.json (формат _snapshot_backup). После JSON-круги
    значения приходят str (uuid/timestamptz/numeric)/int/bool/None/dict/list —
    PG кастует по типу столбца; dict/list _restore оборачивает в Json() (_adapt)."""
    data = json.loads((out_dir / "db_backup.json").read_text(encoding="utf-8"))
    return {t: (v["cols"], v["rows"], v["hash"]) for t, v in data.items()}


def _restore_safe(conn, snap, out_dir=None):
    """Восстановление с гарантией: из памяти -> (при сбое) из бэкапа на диске.

    Обе попытки провалились — RestoreFailedError (прогон обязан остановиться).
    """
    errors = []
    try:
        _restore(conn, snap)
        _verify(conn, snap)
        return
    except Exception as e:
        errors.append(f"memory: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    if out_dir is not None and (out_dir / "db_backup.json").exists():
        try:
            disk_snap = _snap_from_backup(out_dir)
            _restore(conn, disk_snap)
            _verify(conn, disk_snap)
            return
        except Exception as e:
            errors.append(f"disk: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
    raise RestoreFailedError(
        "RESTORE FAILED (memory+disk): " + " | ".join(errors)[:500])


def _snapshot_backup(conn, out_dir):
    """Снапшот ВСЕГДА по полному FK-замкнутому набору: восстановление
    двухфазным сбросом требует замкнутости (иначе DELETE родителя падает по FK)."""
    snap = _snapshot(conn, _ALL_TABLES)
    (out_dir / "db_backup.json").write_text(
        json.dumps({t: {"cols": c,
                        "rows": [[_ser(x) for x in r] for r in rows], "hash": h}
                    for t, (c, rows, h) in snap.items()}, default=str),
        encoding="utf-8")
    return snap


def run_instrument_state(st):
    out_dir = ROOT / "ui-audit" / "states" / st["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = None
    snap = None
    try:
        conn = _connect()
        snap = _snapshot_backup(conn, out_dir)
        if st.get("setup"):
            try:
                _mutate(conn, st["setup"], st.get("setup_params", [()] * len(st["setup"])))
            except Exception:
                conn.rollback()
                raise
        _run_instrument(st["screens"], out_dir)
        return "OK"
    finally:
        if conn is not None and snap is not None:
            try:
                _restore_safe(conn, snap, out_dir)
            finally:
                _release(conn)


# ------------------------------------------------------------------ custom
def _shot(page, shot_dir, key, golden=True, note=""):
    """Скриншот-артефакт + (опционально) сравнение с эталоном.

    Возвращает (name, status, detail) — результат ОБЯЗАН попасть в results
    состояния, иначе эталонное расхождение молча теряется (U-8).
    """
    if not golden:
        page.evaluate(ui_audit.STABILIZE_JS)
        page.wait_for_timeout(150)
        page.screenshot(path=str(shot_dir / f"{key}.png"), full_page=True)
        return (f"screenshot:{key}", "INFO",
                note or "артефакт без эталонного сравнения")
    status, detail = ui_audit.golden_check(page, shot_dir, key, update=False)
    return (f"golden:{key}", status, detail)


def _new_context(p, vp, w, h, extra=None):
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(
        viewport={"width": w, "height": h},
        is_mobile=vp in ("360", "390"), has_touch=vp in ("360", "390"),
        extra_http_headers=extra or {})
    # Токен — в jar браузера (а не extra header): extra header не
    # прикрепляется к form-POST навигациям и их редиректам, из-за чего
    # POST-экраны падали в 401 -> /ui/login (проверено curl'ом: app в норме).
    ctx.add_cookies([{"name": "nextlife_token", "value": TOKEN, "url": BASE}])
    return browser, ctx


def _custom_lightbox_positions(p, vp, w, h, base, shot_dir):
    """463 (20 фото): первое/среднее/последнее + свайп + клавиатура + счётчик."""
    results = []
    browser, ctx = _new_context(p, vp, w, h)
    try:
        page = ctx.new_page()
        page.goto(f"{base}/ui/items/463", wait_until="domcontentloaded")
        page.evaluate(ui_audit.WAIT_IMAGES_JS)
        page.wait_for_timeout(300)
        page.click("#slider-main-img")
        page.wait_for_timeout(250)
        counter = lambda: page.evaluate("() => document.getElementById('lightbox-counter').textContent")
        c_first = counter()
        results.append(_shot(page, shot_dir, f"lb_first_{vp}"))
        for _ in range(9):
            page.click("#lightbox-next")
            page.wait_for_timeout(60)
        c_mid = counter()
        results.append(_shot(page, shot_dir, f"lb_middle_{vp}"))
        for _ in range(10):
            page.click("#lightbox-next")
            page.wait_for_timeout(60)
        c_last = counter()
        results.append(_shot(page, shot_dir, f"lb_last_{vp}"))
        # клавиатура: ArrowLeft -> 19/20, ArrowRight -> 20/20
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(80)
        c_k1 = counter()
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(80)
        c_k2 = counter()
        results.append(("keyboard+counter", "PASS" if (c_first != c_mid != c_last
                                                       and c_k1 != c_last and c_k2 == c_last)
                        else "FAIL",
                        f"first={c_first!r} mid={c_mid!r} last={c_last!r} left={c_k1!r} right={c_k2!r}"))
        # свайп мышью (в base.html mouse-swipe на #lightbox): влево -> next (обёртка 20->1)
        lb = page.query_selector("#lightbox")
        if lb:
            box = lb.bounding_box()
            if box:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                before = counter()
                page.mouse.move(cx + 80, cy)
                page.mouse.down()
                page.mouse.move(cx - 80, cy, steps=6)
                page.mouse.up()
                page.wait_for_timeout(120)
                after = counter()
                results.append(("swipe", "PASS" if before != after else "FAIL", f"{before} -> {after}"))
        # lbSwiped в base.html гаснет через 200ms — ждём дольше, иначе
        # closeLightbox() откажет в закрытии (защита от закрытия хвостом свайпа).
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)
        closed = page.evaluate("() => getComputedStyle(document.getElementById('lightbox')).display === 'none'")
        results.append(("escape-close", "PASS" if closed else "FAIL", ""))
    finally:
        browser.close()
    return results


def _custom_edit_validation(p, vp, w, h, base, shot_dir):
    """259: отправка формы с пустым title. Ожидание: редирект + notice, title НЕ изменён.

    Лот 259 выбран сознательно: edit_lot пишет audit-событие LOT_EDITED по
    целевому лоту, а карточка предмета рендерит ленту аудита (последние
    события, свежая метка времени) — целевой лот НЕ должен фигурировать в
    эталонах других состояний (348/417/463/747/802/106), иначе лента сдвигает
    эталоны между прогоннами. У 259 экранов-эталонов нет, его лента
    незаметна ни в одном скриншоте.

    Эталонное сравнение post-submit снимка НЕ применяется (структурная
    нестабильность: страница содержит ленту аудита целевого лота, которую
    собственные сабмиты состояния расширяют каждый прогон) — снимок
    сохраняется как артефакт, состояние верифицируется функциональными
    проверками. Раскрыто в отчёте N5.

    ВНИМАНИЕ: edit_lot безусловно переводит лот в AWAITING_APPROVAL и снимает
    утверждение — состояние оборачивается снапшотом/восстановлением.
    """
    results = []
    browser, ctx = _new_context(p, vp, w, h)
    try:
        page = ctx.new_page()
        page.goto(f"{base}/ui/lots/259/edit", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        original_title = page.evaluate("() => document.getElementById('lot-title') ? document.getElementById('lot-title').value : ''")
        page.fill("#lot-title", "   ")
        page.click("button[type='submit']")
        page.wait_for_timeout(500)
        url = page.url
        ok_redirect = "/ui/items/259?notice=lot_edited_approval_invalidated" in url
        title_now = page.evaluate("() => { const el = document.querySelector('.fs-xl'); return el ? el.textContent.trim() : ''; }")
        results.append(_shot(page, shot_dir, f"edit_submitted_{vp}", golden=False,
                             note="эталон не применяется: лента аудита лота 259 расширяется "
                                  "собственными сабмитами состояния (append-only, метки времени)"))
        results.append(("edit-submit-redirect", "PASS" if ok_redirect else "FAIL", url))
        results.append(("title-preserved", "PASS" if original_title and original_title in title_now
                        else "FAIL", f"было {original_title!r} -> {title_now!r}"))
        results.append(("field-errors", "INFO",
                        "ошибок валидации по полям в UI нет: пустой title тихо игнорируется edit_lot "
                        "(status->AWAITING_APPROVAL, утверждение снято) — раскрыто в отчёте"))
    finally:
        browser.close()
    return results


def _custom_upload_error(p, vp, w, h, base, shot_dir):
    """348: загрузка .txt -> HTTP 500 + страница ошибки N4-04 (мягкого UI-состояния нет)."""
    results = []
    browser, ctx = _new_context(p, vp, w, h, extra={"Accept": "text/html"})
    try:
        page = ctx.new_page()
        page.goto(f"{base}/ui/items/348", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        bad = ROOT / ".qwen" / "tmp" / "badfile.txt"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not an image", encoding="utf-8")
        page.set_input_files("input[name='files']", str(bad))
        page.click("[data-testid='btn-submit-upload']")
        page.wait_for_timeout(600)
        is_error_page = page.evaluate("() => !!document.querySelector('[data-ui-error]')")
        err_id = page.evaluate(
            "() => { const el = document.querySelector('[data-error-id]'); return el ? el.getAttribute('data-error-id') : null; }")
        id_ok = bool(err_id) and len(err_id) == 12 and all(c in "0123456789abcdef" for c in err_id.lower())
        results.append(("upload-error-id-format", "PASS" if id_ok else "FAIL",
                        f"ID ошибки: {err_id} (12 hex)"))
        # N4-04: каждый сбой получает уникальный случайный ID, видимый в тексте.
        # Нормализуем видимый токен перед эталоном — иначе сравнение между
        # проходами всегда расходится; функциональный формат ID проверен выше.
        page.evaluate("() => { const el = document.querySelector('#nl-err-id code'); "
                      "if (el) el.textContent = '000000000000'; }")
        results.append(_shot(page, shot_dir, f"upload_error_{vp}"))
        results.append(("upload-error-state", "PASS" if is_error_page else "FAIL",
                        f"страница ошибки N4-04: id={err_id} (мягкого UI-состояния «ошибка загрузки» нет — раскрыто в отчёте)"))
    finally:
        browser.close()
    return results


def _custom_deal_modal(p, vp, w, h, base, shot_dir):
    """deal-348-1: модалка открыта (скриншот) и закрывается."""
    results = []
    browser, ctx = _new_context(p, vp, w, h)
    try:
        page = ctx.new_page()
        page.goto(f"{base}/ui/deals/deal-348-1", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        has_modal = page.evaluate("() => !!document.querySelector('.modal-overlay')")
        if not has_modal:
            results.append(("deal-modal", "FAIL", "на экране нет .modal-overlay"))
            return results
        page.evaluate("() => document.querySelector('.modal-overlay').classList.remove('hidden')")
        page.wait_for_timeout(300)
        vis = page.evaluate("() => { const ov = document.querySelector('.modal-overlay'); "
                            "return ov.open === true || getComputedStyle(ov).display !== 'none'; }")
        results.append(_shot(page, shot_dir, f"deal_modal_{vp}"))
        page.evaluate("() => document.querySelector('.modal-overlay').classList.add('hidden')")
        page.wait_for_timeout(200)
        # .modal-overlay — div (не <dialog>), .hidden => display:none !important (app.css)
        closed = page.evaluate("() => { const ov = document.querySelector('.modal-overlay'); "
                               "return ov.classList.contains('hidden') && "
                               "getComputedStyle(ov).display === 'none'; }")
        results.append(("deal-modal-open", "PASS" if vis else "FAIL", "модалка отправки открылась"))
        results.append(("deal-modal-close", "PASS" if closed else "FAIL", ""))
    finally:
        browser.close()
    return results


def _custom_wizard_resume(p, vp, w, h, base, shot_dir):
    """Мастер: прерывание и продолжение.

    POST step1 (создание) -> POST step2 -> POST step3 (grade A + коробка) ->
    GET ?step=3 (возврат на сохранённый шаг: значения должны быть префиллены из БД) -> cleanup.
    Редиректы 303 прибор следует сам; финальный URL — в r.url.
    """
    results = []
    conn = _connect()
    created_code = None
    browser, ctx = _new_context(p, vp, w, h)
    try:
        r1 = ctx.request.post(
            f"{base}/ui/wizard/step/1",
            form={"code": "new", "title": "N5 Resume Test", "category": "general",
                  "notes": "продолжение после прерывания"})
        m = re.search(r"/ui/wizard/([A-Za-z0-9]{2,})\?step=2", r1.url)
        created_code = m.group(1) if m else None
        results.append(("wizard-create", "PASS" if (r1.status == 200 and created_code) else "FAIL",
                        f"status={r1.status} final_url={r1.url!r} code={created_code}"))
        if not created_code:
            return results
        r2 = ctx.request.post(
            f"{base}/ui/wizard/step/2", form={"code": created_code})
        results.append(("wizard-step2", "PASS" if (r2.status == 200 and "?step=3" in r2.url) else "FAIL",
                        f"status={r2.status} final_url={r2.url!r}"))
        r3 = ctx.request.post(
            f"{base}/ui/wizard/step/3",
            form={"code": created_code, "condition_grade": "A", "box_present": "true"})
        results.append(("wizard-step3-save", "PASS" if (r3.status == 200 and "?step=4" in r3.url) else "FAIL",
                        f"status={r3.status} final_url={r3.url!r}"))
        # Прерывание: пользователь уходит и возвращается на шаг 3
        page = ctx.new_page()
        page.goto(f"{base}/ui/wizard/{created_code}?step=3", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        grade = page.evaluate("() => document.getElementById('wz-condition') ? document.getElementById('wz-condition').value : ''")
        box = page.evaluate("() => { const c = document.querySelector('input[name=box_present]'); return c ? c.checked : null; }")
        results.append(_shot(page, shot_dir, f"wizard_resume_{vp}"))
        results.append(("wizard-resume", "PASS" if (grade == "A" and box) else "FAIL",
                        f"step3: grade={grade!r} box_present={box} (данные сохранены после прерывания)"))
    finally:
        browser.close()
        if created_code:
            cur = conn.cursor()
            for t, col in (("approval", "item_code"), ("item_photo", "item_code"),
                           ("lot", "item_id"), ("item", "code")):
                cur.execute(f"DELETE FROM {t} WHERE {col} = %s", (created_code,))
            conn.commit()
            results.append(("wizard-cleanup", "PASS", f"тестовый лот {created_code} удалён"))
        _release(conn)
    return results


CUSTOM_STATES = {
    "lightbox-positions": _custom_lightbox_positions,
    "edit-validation": _custom_edit_validation,
    "upload-error": _custom_upload_error,
    "deal-modal": _custom_deal_modal,
    "wizard-resume": _custom_wizard_resume,
}


def run_custom_state(st, p):
    out_dir = ROOT / "ui-audit" / "states" / st["id"]
    shots = out_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    all_results = []
    fn = CUSTOM_STATES[st["fn"]]
    conn = None
    snap = None
    try:
        # Полная FK-замкнутая защита: кастомные состояния могут мутировать
        # (edit-validation, wizard-resume) — снапшот по всему набору.
        conn = _connect()
        snap = _snapshot_backup(conn, out_dir)
        for vp in VPS:
            w, h = ui_audit.VIEWPORTS[vp]
            for name, status, detail in fn(p, vp, w, h, BASE, shots):
                all_results.append((vp, name, status, detail))
    finally:
        if conn is not None and snap is not None:
            try:
                _restore_safe(conn, snap, out_dir)
            finally:
                _release(conn)
    # N6-01: персистентный артефакт для каталога проверок и контроля полноты.
    (out_dir / "results.json").write_text(
        json.dumps([{"vp": vp, "check": name, "status": status, "detail": detail}
                    for vp, name, status, detail in all_results],
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    return all_results


# ------------------------------------------------------------- state recipes
# Полное удаление контента (порядок: дети раньше родителей; FK-замкнутый набор).
# item_photo имеет самоссылку derived_from_id (NO ACTION): полная очистка
# идёт от листьев цепочки (PUBLISH -> PROCESSED -> ORIGINAL), иначе удаление
# источника при живом производном фото нарушает FK. Три прохода + остаток.
_PHOTO_PEEL = ("DELETE FROM item_photo WHERE derived_from_id IS NOT NULL AND "
               "NOT EXISTS (SELECT 1 FROM item_photo d WHERE d.derived_from_id = item_photo.id)")
_FULL_DELETE = [
    "DELETE FROM message",
    "DELETE FROM thread",
    "DELETE FROM payment_option",
    "DELETE FROM handover",
    "DELETE FROM settlement",
    "DELETE FROM deal",
    "DELETE FROM delivery_option",
    "DELETE FROM approval",
    "DELETE FROM item_defect",
    "DELETE FROM lot_pricing",
    "DELETE FROM price_history",
    "DELETE FROM research_result",
    "DELETE FROM research_run",
    "DELETE FROM media_variant",
    "DELETE FROM media_master",
    "DELETE FROM media_original",
    _PHOTO_PEEL,
    _PHOTO_PEEL,
    _PHOTO_PEEL,
    "DELETE FROM item_photo",
    "DELETE FROM listing",
    "DELETE FROM lot",
    "DELETE FROM item",
]
# Снапшот/восстановление — по всем таблицам набора (полная консистентность N5).
_ALL_TABLES = list(RESTORE_ORDER)

LONGTITLE = ("Сверхдлинное название предмета для проверки переноса текста "
             "без горизонтального переполнения на узком мобильном экране 360 пикселей "
             "с очень длинным неперерываемым сегментом "
             "abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz0123456789")

STATES = [
    # ---- без мутаций (живые данные) ----
    {"id": "item-nodefects", "kind": "instrument", "desc": "item: нет дефектов (лот 417, запись defects отсутствует)",
     "screens": [("item[417]", "/ui/items/417", "")]},
    {"id": "item-defects", "kind": "instrument", "desc": "item: есть дефекты (лот 802, царапина 3 мм)",
     "screens": [("item[802]", "/ui/items/802", "")]},
    {"id": "item-nonotes", "kind": "instrument", "desc": "item: нет заметок, описание — fallback (лот 106)",
     "screens": [("item[106]", "/ui/items/106", "")]},
    {"id": "item-manyphotos", "kind": "instrument", "desc": "item/медиа/лайтбокс: много фото (лот 463, 20 шт)",
     "screens": [("item[463]", "/ui/items/463", ""), ("item-media[463]", "/ui/items/463", "item"),
                 ("lightbox[463]", "/ui/items/463", "item")]},
    {"id": "wizard-steps", "kind": "instrument", "desc": "мастер: все 5 шагов (лот 348)",
     "screens": [(f"wizard-step{i}", f"/ui/wizard/348?step={i}", "") for i in range(1, 6)]},
    # ---- мутации (бэкап + восстановление с проверкой) ----
    {"id": "item-nophoto", "kind": "instrument", "desc": "item/медиа: нет фото (лот 747)",
     # Срезка цепочек 747 «с листьев», 3 круга = максимальная глубина
     # (ORIGINAL → PROCESSED → PUBLISH, + DEFECT при наличии): self-FK
     # derived_from_id (NO ACTION) запрещает удалять строку с живыми
     # детьми. Раньше состояние полагалось на исходную пустоту 747 —
     # после реальной загрузки фото владельцем оно давало бы расхождение.
     "setup": [
         "DELETE FROM item_photo WHERE item_code = '747' AND id NOT IN "
         "(SELECT derived_from_id FROM item_photo WHERE derived_from_id IS NOT NULL)",
     ] * 3,
     "screens": [("item[747]", "/ui/items/747", ""), ("item-media[747]", "/ui/items/747", "item")]},
    {"id": "items-empty", "kind": "instrument", "desc": "лента/дашборд/архив/треды/входящие/мастер: пусто",
     "tables": _ALL_TABLES, "setup": _FULL_DELETE,
     "screens": [("items", "/ui/items", ""), ("dashboard", "/ui/dashboard", ""),
                 ("archive", "/ui/archive", ""), ("threads", "/ui/threads", ""),
                 ("inbox", "/ui/inbox", ""), ("wizard", "/ui/wizard", "")]},
    {"id": "items-one", "kind": "instrument", "desc": "лента: один предмет (348)",
     "tables": _ALL_TABLES,
     "setup": [
         # дети раньше родителей; остаётся только 348 + deal-348-1
         "DELETE FROM message",
         "DELETE FROM thread",
         "DELETE FROM payment_option WHERE lot_id IN (SELECT id FROM lot WHERE item_id <> '348')",
         "DELETE FROM handover WHERE deal_id <> 'deal-348-1'",
         "DELETE FROM settlement WHERE deal_id <> 'deal-348-1'",
         "DELETE FROM deal WHERE id <> 'deal-348-1'",
         "DELETE FROM item_defect WHERE item_code <> '348'",
         "DELETE FROM lot_pricing WHERE item_code <> '348'",
         "DELETE FROM price_history WHERE item_code <> '348'",
         "DELETE FROM research_result WHERE run_id IN (SELECT id FROM research_run WHERE item_code <> '348')",
         "DELETE FROM research_run WHERE item_code <> '348'",
         "DELETE FROM media_variant WHERE master_id IN "
         "(SELECT id FROM media_master WHERE original_id IN (SELECT id FROM media_original WHERE item_code <> '348'))",
         "DELETE FROM media_master WHERE original_id IN (SELECT id FROM media_original WHERE item_code <> '348')",
         "DELETE FROM media_original WHERE item_code <> '348'",
         "DELETE FROM delivery_option WHERE item_code <> '348'",
         "DELETE FROM approval WHERE item_code <> '348'",
         "DELETE FROM item_photo WHERE item_code <> '348'",
         "DELETE FROM listing WHERE item_code <> '348'",
         "DELETE FROM lot WHERE item_id <> '348'",
         "DELETE FROM item WHERE code <> '348'",
     ],
     "screens": [("items", "/ui/items", ""), ("dashboard", "/ui/dashboard", ""),
                 ("item[348]", "/ui/items/348", "")]},
    {"id": "items-twenty", "kind": "instrument", "desc": "лента: 20 предметов (12 реальных + 8 синтетических)",
     "tables": ["item"],
     "setup": [
         # created_at разнесены на 1 c, чтобы порядок ленты (ORDER BY created_at DESC)
         # был детерминирован между прогонами (равные отметки -> случайный порядок).
         "INSERT INTO item (code, kind, title, category_key, attrs, condition_key, defects, status, "
         "target_price, target_currency, created_at, updated_at) VALUES "
         + ",".join(
             f"('S{i:02d}','SELL','Синтетический предмет {i} (N5-тест списка из 20)','general','{{}}','used','[]','DRAFT',{100*i},'CZK',"
             f"(now() - interval '{8 - i} seconds'),(now() - interval '{8 - i} seconds'))"
             for i in range(1, 9)
         ),
     ],
     "screens": [("items", "/ui/items", "")]},
    {"id": "items-longtitle", "kind": "instrument", "desc": "лента/карточка: очень длинное название (348)",
     "tables": ["item"],
     "setup": ["UPDATE item SET title = %(t)s WHERE code = '348'"],
     "setup_params": [{"t": LONGTITLE}],
     "screens": [("items", "/ui/items", ""), ("item[348]", "/ui/items/348", "")]},
    {"id": "media-one", "kind": "instrument", "desc": "медиа/лайтбокс: одно фото (348: 2->1)",
     "tables": ["item_photo"],
     "setup": ["DELETE FROM item_photo WHERE id IN "
               "(SELECT id FROM (SELECT id FROM item_photo WHERE item_code='348' "
               "ORDER BY created_at DESC LIMIT 1) s)"],
     "screens": [("item[348]", "/ui/items/348", ""), ("lightbox[348]", "/ui/items/348", "item")]},
    {"id": "media-twelve", "kind": "instrument", "desc": "медиа: двенадцать фото (463: 20->12, остаются первые 12 по created_at)",
     "tables": ["item_photo"],
     # derived_from_id — NO ACTION: удаляем от листьев цепочки (PUBLISH -> PROCESSED -> ORIGINAL),
     # иначе удаление источника при живом производном фото нарушит FK. 3 прохода + остаток.
     "setup": [
         "DELETE FROM item_photo WHERE item_code='463' AND id NOT IN "
         "(SELECT id FROM (SELECT id FROM item_photo WHERE item_code='463' "
         "ORDER BY created_at ASC LIMIT 12) k) AND derived_from_id IS NOT NULL AND "
         "NOT EXISTS (SELECT 1 FROM item_photo d WHERE d.derived_from_id = item_photo.id)",
         "DELETE FROM item_photo WHERE item_code='463' AND id NOT IN "
         "(SELECT id FROM (SELECT id FROM item_photo WHERE item_code='463' "
         "ORDER BY created_at ASC LIMIT 12) k) AND derived_from_id IS NOT NULL AND "
         "NOT EXISTS (SELECT 1 FROM item_photo d WHERE d.derived_from_id = item_photo.id)",
         "DELETE FROM item_photo WHERE item_code='463' AND id NOT IN "
         "(SELECT id FROM (SELECT id FROM item_photo WHERE item_code='463' "
         "ORDER BY created_at ASC LIMIT 12) k) AND derived_from_id IS NOT NULL AND "
         "NOT EXISTS (SELECT 1 FROM item_photo d WHERE d.derived_from_id = item_photo.id)",
         "DELETE FROM item_photo WHERE item_code='463' AND id NOT IN "
         "(SELECT id FROM (SELECT id FROM item_photo WHERE item_code='463' "
         "ORDER BY created_at ASC LIMIT 12) k) AND "
         "NOT EXISTS (SELECT 1 FROM item_photo d WHERE d.derived_from_id = item_photo.id)",
     ],
     "screens": [("item[463]", "/ui/items/463", ""), ("item-media[463]", "/ui/items/463", "item")]},
    {"id": "shipping-several", "kind": "instrument", "desc": "доставка: несколько опций (463: 1->3)",
     "tables": ["delivery_option"],
     "setup": [
         "INSERT INTO delivery_option (id, item_code, service_id, carrier_key, service_name, "
         "price, is_manual_price, is_enabled, broker_key, created_at) "
         "SELECT gen_random_uuid(), item_code, service_id, carrier_key, service_name || ' (N5-2)', "
         "price + 20, false, true, broker_key, now() FROM delivery_option WHERE item_code='463'",
         "INSERT INTO delivery_option (id, item_code, service_id, carrier_key, service_name, "
         "price, is_manual_price, is_enabled, broker_key, created_at) "
         "SELECT gen_random_uuid(), item_code, service_id, carrier_key, service_name || ' (N5-3)', "
         "price + 40, false, true, broker_key, now() FROM delivery_option WHERE item_code='463'",
     ],
     "screens": [("shipping[463]", "/ui/lots/463/shipping", "")]},
    {"id": "draft-edited", "kind": "instrument", "desc": "черновик: отредактировано (348: final_text <- текст редакции оператора)",
     "tables": ["item"],
     # to_jsonb требует типизированный аргумент: литерал без каста — unknown
     # (PG: «could not determine polymorphic type»). ::text обязателен.
     "setup": ["UPDATE item SET attrs = jsonb_set(attrs, '{final_text}', "
               "to_jsonb('Отредактировано оператором: состояние проверено, фото соответствуют описанию. Цена подтверждена.'::text)) "
               "WHERE code='348'"],
     "screens": [("draft[348]", "/ui/draft/348", "")]},
    {"id": "draft-limit", "kind": "instrument", "desc": "черновик: превышен лимит символов (348: 3000 -> ~6700)",
     "tables": ["item"],
     "setup": ["UPDATE item SET attrs = jsonb_set(attrs, '{final_text}', "
               "to_jsonb(repeat('Тестовый текст для проверки превышения лимита символов. ', 120))) "
               "WHERE code='348'"],
     "screens": [("draft[348]", "/ui/draft/348", "")]},
    {"id": "dashboard-all-ok", "kind": "instrument", "desc": "дашборд: всё в порядке (нет активных сделок); архив заполнен",
     "tables": ["deal"],
     "setup": ["UPDATE deal SET status='COMPLETED', closed_at = now() WHERE status='ACTIVE'"],
     "screens": [("dashboard", "/ui/dashboard", ""), ("archive", "/ui/archive", "")]},
    {"id": "archive-empty", "kind": "instrument", "desc": "архив: пусто (нет COMPLETED-сделок и ARCHIVED/SOLD лотов)",
     "tables": ["deal"],
     "setup": ["UPDATE deal SET status='ACTIVE', closed_at = NULL WHERE status='COMPLETED'"],
     "screens": [("archive", "/ui/archive", "")]},
    {"id": "threads-fraud", "kind": "instrument", "desc": "треды: подозрение на мошенничество (thread-802-1)",
     "tables": ["message", "thread"],
     "setup": [
         "UPDATE message SET source_evidence = source_evidence || "
         "'{\"fraud_suspected\": true, \"fraud_reasons\": [\"перевод на карту до получения товара\"]}'::jsonb, "
         "escalated = TRUE WHERE thread_id='thread-802-1'",
         "UPDATE thread SET status='FRAUD_SUSPECTED' WHERE id='thread-802-1'",
     ],
     "screens": [("threads", "/ui/threads", ""), ("dashboard", "/ui/dashboard", "")]},
    {"id": "threads-unread", "kind": "instrument", "desc": "треды: непрочитанные/без ответа (эскалация, thread-802-1)",
     "tables": ["message"],
     "setup": ["UPDATE message SET answered_by = NULL, escalated = TRUE WHERE thread_id='thread-802-1'"],
     "screens": [("threads", "/ui/threads", "")]},
    # ---- кастомные (Playwright) ----
    {"id": "lightbox-positions", "kind": "custom", "fn": "lightbox-positions",
     "desc": "лайтбокс: первое/среднее/последнее, свайп, клавиатура, счётчик (463)"},
    {"id": "edit-validation", "kind": "custom", "fn": "edit-validation",
     "tables": ["item", "lot", "approval"],
     "desc": "правка: валидная форма — отправка (259, без эталонных экранов: лента аудита целевого лота не входит ни в один эталон); ошибки валидации по полям в UI не предусмотрены"},
    {"id": "upload-error", "kind": "custom", "fn": "upload-error",
     "desc": "медиа: ошибка загрузки одного файла (348, .txt -> 500 + страница N4-04)"},
    {"id": "deal-modal", "kind": "custom", "fn": "deal-modal",
     "desc": "сделка: модальное окно отправки (deal-348-1)"},
    {"id": "wizard-resume", "kind": "custom", "fn": "wizard-resume",
     "desc": "мастер: прерывание и продолжение (создание -> step3)"},
]


def main():
    ap = argparse.ArgumentParser(description="N5 state runner")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--state", action="append", default=[],
                    help="id состояния; может быть повторён несколько раз")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for s in STATES:
            print(f"{s['id']:20} [{s['kind']:10}] {s['desc']}")
        return 0
    ids = [s["id"] for s in STATES] if a.all else list(dict.fromkeys(a.state))
    if not ids:
        ap.print_help()
        return 2

    summary = []
    p = None
    try:
        for sid in ids:
            st = next((s for s in STATES if s["id"] == sid), None)
            if st is None:
                print(f"!! unknown state {sid}")
                continue
            print(f"\n=== STATE {sid}: {st['desc']} ===")
            try:
                if st["kind"] == "instrument":
                    # Прибор сам запускает собственный Playwright — внешний
                    # контекст здесь не нужен (избегание вложенных sync_playwright).
                    run_instrument_state(st)
                    rep = ROOT / "ui-audit" / "states" / sid / "report.md"
                    line = ""
                    if rep.exists():
                        for l in rep.read_text(encoding="utf-8").splitlines():
                            if l.startswith("- FAIL:"):
                                line = l
                                break
                    ok = "FAIL: 0" in line
                    summary.append((sid, "PASS" if ok else "FAIL", line.strip() or "no report"))
                    print(f"  -> ui-audit/states/{sid}/report.md | {line.strip() or 'NO REPORT'}")
                else:
                    if p is None:
                        p = sync_playwright().start()
                    res = run_custom_state(st, p)
                    bad = [r for r in res if r[2] == "FAIL"]
                    for vp, name, status, detail in res:
                        print(f"  [{vp}] {name}: {status}" + (f" — {detail}" if detail else ""))
                    summary.append((sid, "PASS" if not bad else "FAIL",
                                    f"{len(res) - len(bad)}/{len(res)} ok"))
            except RestoreFailedError as e:
                # ФАТАЛЬНО: БД может быть разрушена. Дальнейшие состояния
                # запрет — они «проверят» и закрепят разрушение (инцидент
                # 2026-09-11). Прогон останавливается, exit 3.
                summary.append((sid, "FATAL", str(e)[:160]))
                print(f"  !!! FATAL RESTORE: {e}")
                print("  !!! ПРОГОН ОСТАНОВЛЕН: восстановите БД и повторите с чистого состояния.")
                break
            except Exception as e:
                summary.append((sid, "ERROR", str(e)[:160]))
                print(f"  !! ERROR: {e}")
    finally:
        if p is not None:
            p.stop()

    print("\n=== SUMMARY ===")
    for sid, status, det in summary:
        print(f"{status:8} {sid:20} {det}")
    if any(s == "FATAL" for _, s, _ in summary):
        return 3
    if any(s in ("FAIL", "ERROR") for _, s, _ in summary):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
