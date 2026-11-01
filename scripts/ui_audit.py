#!/usr/bin/env python3
"""N1. UI-прибор NextLife — проверки N1-01..N1-07 (N1-08 = scripts/check_css_contract.py).

  N1-01 ошибки/предупреждения консоли + сетевые 4xx/5xx (включая CSS/JS/шрифты/изображения)
  N1-02 переполнение по ЭЛЕМЕНТАМ: rect.right > viewport
  N1-03 изображения: clientWidth/naturalWidth vs контейнер и вьюпорт (после полной загрузки)
  N1-04 цели нажатия: видимы, не перекрыты, >= 44x44, есть доступное имя
  N1-05 элементы .hidden обязаны быть display:none; модалки невидимы в исходном состоянии
  N1-06 директивы библиотеки => библиотека определена в window
  N1-07 эталонные скриншоты: сравнение с допуском, при расхождении — diff-картинка

Запуск:
  python3 scripts/ui_audit.py --url http://localhost:8081 --token <token> --out ui-audit/run
  python3 scripts/ui_audit.py --update-golden     # пересоздать эталоны
  python3 scripts/ui_audit.py --only items,shipping   # подмножество экранов
  python3 scripts/ui_audit.py --offline           # N3-01: прервать внешние запросы, доказать офлайн
  python3 scripts/ui_audit.py --selftest --out ui-audit/run   # N6-02: самопроверка прибора

Коды выхода: 0 — все PASS/GOLDEN; 1 — есть FAIL; 2 — ошибка окружения.
BLOCKED (нет данных/маршрута) и SKIP не считаются FAIL, но видны в отчёте.

Пояснения к методике (честные допущения, раскрыты в отчёте):
  N1-02: элемент в горизонтально скроллящемся контейнере (.filter-pills,
         .thumb-strip), который сам умещается в вьюпорт, не считается
         переполнением (паттерн «лента с прокруткой», а не дефект).
  N1-04: цель нажатия чекбокса/радио меряется по ассоциированному <label>
         (вся строка), а не по 20px-боксу (WCAG 2.5.5/2.5.8).
  N1-04: доступное имя берётся по стандартной цепочке — текст,
         ассоциированный <label for>, aria-label, title, alt, name.
  N1-04: если цель перекрыта fixed/sticky-элементом (нижняя навигация),
         прибор скроллит её в центр вьюпорта и замеряет повторно; FAIL —
         только если цель перекрыта при ЛЮБОЙ прокрутке (недоступна).
  N1-06: Alpine и HTMX подключены в base.html, поэтому обязаны быть
         определены на КАЖДОМ экране (а не только там, где есть директивы).
  N3-04: на экранах с .modal-overlay проверяются четыре сценария:
         закрытие по кнопке (только безопасная: «Отмена/Закрыть» или
         type="button", никогда type="submit"), по клику вне окна, по
         Escape + возврат фокуса к элементу до открытия и блокировка
         прокрутки фона при открытом окне.
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
_lib = ROOT / "lib" / "playwright_libs"
if _lib.exists():
    os.environ["LD_LIBRARY_PATH"] = f"{_lib}:{os.environ.get('LD_LIBRARY_PATH', '')}".strip(":")

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

VIEWPORTS = {"360": (360, 800), "390": (390, 844), "768": (768, 1024), "1280": (1280, 900)}
GOLDEN_DIR = ROOT / "ui-audit" / "golden"
PIXEL_TOL = 32      # допуск на пиксель (антиалиасинг)
DIFF_FRAC = 0.0005  # доля изменившихся пикселей, считающаяся расхождением (0.05%)
TAP_MIN = 44        # минимальная цель нажатия, px

BASE_SCREENS = [
    ("dashboard", "/ui/dashboard"),
    ("items", "/ui/items"),
    ("threads", "/ui/threads"),
    ("inbox", "/ui/inbox"),
    ("archive", "/ui/archive"),
    ("wizard", "/ui/wizard"),
]

# Единый замер: переполнение/изображения/тап-таргеты/скрытые/библиотеки.
MEASURE_JS = r"""
(vw) => {
  const out = {overflow: [], images: [], taps: [], hidden: [], mods: [], alpine: {used: false, defined: false}, htmx: {used: false, defined: false}};
  const sel = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    ((typeof el.className === 'string' && el.className) ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '');
  // N1-02: переполнение по элементам. Документированное исключение:
  // элемент внутри горизонтально скроллящегося контейнера (.filter-pills,
  // .thumb-strip), который сам умещается в вьюпорт — переполнение
  // содержимое «содержится» контейнером (паттерн, а не дефект).
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || r.right <= vw + 1) return;
    let anc = el.parentElement, contained = false;
    while (anc && anc !== document.body) {
      const st = getComputedStyle(anc);
      if (st.overflowX === 'auto' || st.overflowX === 'scroll') {
        const ar = anc.getBoundingClientRect();
        if (ar.right <= vw + 1) { contained = true; break; }
      }
      anc = anc.parentElement;
    }
    if (!contained) out.overflow.push(sel(el) + ' right=' + Math.round(r.right));
  });
  document.querySelectorAll('img').forEach(img => {
    const r = img.getBoundingClientRect();
    const cw = img.parentElement ? img.parentElement.getBoundingClientRect().width : vw;
    if (r.width > 0 && (img.clientWidth > cw + 1 || img.clientWidth > vw))
      out.images.push((img.src || '').slice(0, 50) + ' clientW=' + img.clientWidth + ' containerW=' + Math.round(cw) + ' natW=' + img.naturalWidth);
  });
  document.querySelectorAll('a, button, [role="button"], input, select, textarea, [tabindex]').forEach(el => {
    const isCheck = el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio');
    // N1-04: цель нажатия чекбокса/радио — ассоциированный label (вся строка),
    // а не 20px-я сам бокс (WCAG 2.5.5/2.5.8: target = label row).
    let target = el;
    if (isCheck) {
      let label = null;
      if (el.id) { try { label = document.querySelector('label[for="' + el.id + '"]'); } catch (e) {} }
      if (!label && el.closest) label = el.closest('label');
      if (label) target = label;
    }
    const r0 = target.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || r0.width < 1 || r0.height < 1) return;
    const inVp = (x, y) => x >= 0 && y >= 0 && x <= vw && y <= window.innerHeight;
    const probeAt = (rect) => {
      const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
      if (!inVp(cx, cy)) return null;
      return document.elementFromPoint(cx, cy);
    };
    let top = probeAt(r0);
    let covered = !!top && top !== target && !target.contains(top) && !top.contains(el) &&
      getComputedStyle(top).pointerEvents !== 'none';
    // Если цель перекрыта fixed/sticky-элементом (нижняя навигация), пробуем
    // прокрутить её в центр вьюпорта: цель «перекрыта» только если к ней
    // невозможно прокрутиться (иначе это нормальное поведение fixed-навигации).
    // Проверяем всю цепочку предков top: сам top может быть static (nav-item
    // внутри fixed .bottom-nav).
    const inFixedChain = (node) => {
      let n = node;
      while (n && n !== document.documentElement) {
        const ps = getComputedStyle(n).position;
        if (ps === 'fixed' || ps === 'sticky') return true;
        n = n.parentElement;
      }
      return false;
    };
    if (covered && inFixedChain(top)) {
      const docEl = document.documentElement;
      const prevSB = docEl.style.scrollBehavior;
      docEl.style.scrollBehavior = 'auto'; // мгновенный скролл (в css smooth)
      target.scrollIntoView({ block: 'center' });
      top = probeAt(target.getBoundingClientRect());
      covered = !!top && top !== target && !target.contains(top) && !top.contains(el) &&
        getComputedStyle(top).pointerEvents !== 'none';
      window.scrollTo(0, 0);
      docEl.style.scrollBehavior = prevSB;
    }
    // Доступное имя: текст / label[for] / aria-label / title / alt / name
    // (стандартная цепочка accessible name, включая ассоциированный label).
    let labelEl = null;
    if (el.id) { try { labelEl = document.querySelector('label[for="' + el.id + '"]'); } catch (e) {} }
    const name = (isCheck && target !== el && target.textContent) ? target.textContent.trim()
      : ((el.textContent || '').trim() || (labelEl ? labelEl.textContent.trim() : '') ||
         el.getAttribute('aria-label') ||
         el.getAttribute('title') || el.getAttribute('alt') || (el.name || ''));
    const prob = [];
    if (r0.width < TAP_MIN - 0.5 || r0.height < TAP_MIN - 0.5) prob.push(r0.width + 'x' + r0.height + 'px<44');
    if (covered) prob.push('covered-by:' + (top ? top.tagName.toLowerCase() : '?'));
    if (!name) prob.push('no-accessible-name');
    if (prob.length) out.taps.push(sel(el) + ' [' + prob.join(', ') + ']');
  });
  document.querySelectorAll('.hidden, [data-hidden]').forEach(el => {
    if (getComputedStyle(el).display !== 'none') out.hidden.push(sel(el) + ' display=' + getComputedStyle(el).display);
  });
  document.querySelectorAll('.modal-overlay, [role="dialog"]').forEach(el => {
    if (getComputedStyle(el).display !== 'none') out.mods.push(sel(el) + ' visible-in-initial-state');
  });
  out.has_modal = !!document.querySelector('.modal-overlay');
  out.alpine.used = !!document.querySelector('[x-data], [x-for], [x-if], [x-show], [x-model]');
  out.alpine.defined = typeof window.Alpine !== 'undefined';
  out.htmx.used = !!document.querySelector('[hx-get], [hx-post], [hx-put], [hx-delete], [hx-trigger]');
  out.htmx.defined = typeof window.htmx !== 'undefined';
  ['overflow', 'taps'].forEach(k => out[k] = out[k].slice(0, 8));
  ['images', 'hidden', 'mods'].forEach(k => out[k] = out[k].slice(0, 6));
  return out;
}
"""
MEASURE_JS = MEASURE_JS.replace("TAP_MIN", str(TAP_MIN))

WAIT_IMAGES_JS = r"""
() => Promise.race([
  Promise.all(Array.from(document.images).map(img => img.complete ? null :
    new Promise(res => { img.addEventListener('load', res, {once: true}); img.addEventListener('error', res, {once: true}); }))),
  new Promise(res => setTimeout(res, 5000))
])
"""

STABILIZE_JS = r"""
() => {
  document.getAnimations().forEach(a => { try { a.finish(); } catch (e) {} });
  const s = document.createElement('style');
  s.id = '__audit_stabilize';
  s.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
  document.head.appendChild(s);
  window.scrollTo(0, 0);
}
"""

# N3-04: четыре сценария модального окна (кнопка / клик вне окна / Escape /
# возврат фокуса) + блокировка прокрутки фона. Открывает модалку снятием
# .hidden (так открывают все наши модалки), безопасная кнопка закрытия —
# только «Отмена/Закрыть» или type="button" (никогда type="submit").
MODAL_CHECK_JS = r"""
async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const overlay = document.querySelector('.modal-overlay');
  if (!overlay) return { skipped: true };
  const box = overlay.querySelector('.modal-box');
  const cands = Array.from(document.querySelectorAll('a[href], button, input, select, textarea'))
    .filter(el => !overlay.contains(el) && el.getClientRects().length > 0);
  const pre = cands[0];
  if (!pre) return { skipped: 'no-pre-focus-element' };
  pre.focus();
  await sleep(30);
  if (document.activeElement !== pre) return { skipped: 'pre-focus-did-not-land' };
  const out = {};
  const open = async () => { overlay.classList.remove('hidden'); await sleep(80); };
  const isClosed = () => overlay.classList.contains('hidden');
  await open();
  out.focusInside = overlay.contains(document.activeElement);
  out.bgLocked = document.body.style.overflow === 'hidden';
  // 1) Escape
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
  await sleep(80);
  out.escapeClosed = isClosed();
  // 2) клик вне окна (точка в углу подложки — вне .modal-box)
  await open();
  const or = overlay.getBoundingClientRect();
  const pt = document.elementFromPoint(or.left + 2, or.top + 2);
  if (pt) pt.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: or.left + 2, clientY: or.top + 2 }));
  await sleep(80);
  out.outsideClosed = isClosed();
  // 3) кнопка закрытия (безопасная: текст Отмена/Закрыть/CANCEL или type="button")
  await open();
  const btns = Array.from(box ? box.querySelectorAll('button') : []);
  let btn = btns.find(b => /отмена|закрыть|cancel|close/i.test(((b.textContent || '') + ' ' + (b.getAttribute('data-testid') || '')).trim()));
  if (!btn) btn = btns.find(b => b.type === 'button');
  if (btn) {
    btn.click();
    await sleep(80);
    out.buttonClosed = isClosed();
    out.closeBtn = ((btn.textContent || '').trim() || 'type=button').slice(0, 20);
  } else {
    out.buttonClosed = false;
    out.closeBtn = 'no-safe-close-button';
  }
  // 4) фокус вернулся к элементу до открытия
  out.focusReturned = document.activeElement === pre;
  out.bgUnlocked = document.body.style.overflow === '';
  return out;
}
"""


def _req_get(context, url: str, tries: int = 2):
    """GET с одним повтором при сетевом сбое (ECONNRESET на keep-alive).

    Возвращает response или None, если обе попытки упали с сетевой ошибкой.
    Это устойчивость прибора: транзитный сброс соединения не обязан ронять
    проверку как сбой экрана (U-8: прибор должен падать на реальных дефектах).
    """
    for _ in range(tries):
        try:
            return context.request.get(url, timeout=15000)
        except Exception:
            continue
    return None


def discover_params(context, base: str) -> dict:
    """Фактические коды лотов/сделок/маркетплейсов из живого приложения и config.

    Лоты — из ленты /ui/items. Лоты с фото — те, чей экран карточки отдаёт
    слайдер (нужен для состояний лайтбокса/медиа). Сделки — из ссылок archive
    (COMPLETED) и через редирект /ui/lots/{code}/deal (последняя сделка лота,
    включая ACTIVE).
    """
    p = {"codes": [], "photo_codes": [], "deals": [], "marketplaces": []}
    resp = _req_get(context, f"{base}/ui/items")
    if resp is not None:
        p["codes"] = list(dict.fromkeys(re.findall(r'/ui/items/([A-Za-z0-9]{2,})', resp.text())))
    for c in p["codes"]:
        resp = _req_get(context, f"{base}/ui/items/{c}")
        if resp is not None and 'id="slider-main-img"' in resp.text():
            p["photo_codes"].append(c)
    resp = _req_get(context, f"{base}/ui/archive")
    if resp is not None:
        p["deals"] = list(dict.fromkeys(re.findall(r'/ui/deals/([A-Za-z0-9-]{8,})', resp.text())))
    for c in p["codes"]:
        resp = _req_get(context, f"{base}/ui/lots/{c}/deal")
        if resp is not None:
            m = re.search(r'/ui/deals/([A-Za-z0-9-]{8,})', resp.url or "")
            if m:
                p["deals"].append(m.group(1))
    p["deals"] = list(dict.fromkeys(p["deals"]))
    for y in sorted((ROOT / "config" / "marketplaces").glob("*.yaml")):
        m = re.search(r'^key:\s*(\S+)', y.read_text(encoding="utf-8"), re.M)
        if m:
            p["marketplaces"].append(m.group(1))
    return p


def build_screens(p: dict) -> list[tuple[str, str, str]]:
    """(имя, путь, группа) — 13 экранов ТЗ + inbox; медиа/лайтбокс — состояния item.

    Лот для item-экранов выбирается с фото (иначе лайтбокс/медиа — SKIP).
    Все обнаруженные сделки добавляются отдельными экранами (покрытие состояний).
    """
    lot = (p["photo_codes"] or p["codes"] or [None])[0]
    s = [(n, path, "") for n, path in BASE_SCREENS]
    if lot:
        s += [
            (f"item[{lot}]", f"/ui/items/{lot}", "item"),
            (f"item-media[{lot}]", f"/ui/items/{lot}", "item"),
            (f"lightbox[{lot}]", f"/ui/items/{lot}", "item"),
            (f"draft[{lot}]", f"/ui/draft/{lot}", ""),
            (f"shipping[{lot}]", f"/ui/lots/{lot}/shipping", ""),
            (f"edit[{lot}]", f"/ui/lots/{lot}/edit", ""),
        ]
        for mp in p["marketplaces"]:
            s.append((f"preview[{lot}/{mp}]", f"/ui/items/{lot}/preview/{mp}", ""))
    else:
        s += [(n, f"/ui/{n}/<NO_LOT>", "blocked") for n in ("item", "draft", "shipping", "edit")]
    for deal in p["deals"]:
        s.append((f"deal[{deal[:8]}]", f"/ui/deals/{deal}", ""))
    if not p["deals"]:
        s.append(("deal", "/ui/deals/<NO_DEAL>", "blocked"))
    return s


def _compare_images(a: Image.Image, b: Image.Image, diff_path: Path | None = None) -> tuple[str, str]:
    """Сравнение двух изображений по пиксельному допуску. diff_path — куда сохранить
    усиленное различие при расхождении (если задан)."""
    if a.size != b.size:
        return ("FAIL", f"размер {a.size[0]}x{a.size[1]} != эталон {b.size[0]}x{b.size[1]}")
    diff = ImageChops.difference(a, b).convert("L")
    mask = diff.point(lambda v: 255 if v > PIXEL_TOL else 0)
    changed = mask.histogram()[255]
    frac = changed / (a.size[0] * a.size[1])
    if frac <= DIFF_FRAC:
        return ("PASS", f"совпадение (изменено {frac:.4%} пикселей)")
    if diff_path is not None:
        vis = ImageChops.difference(a, b).point(lambda v: min(255, v * 8))
        vis.save(diff_path)
        return ("FAIL", f"расхождение {frac:.2%} пикселей ({changed}) — diff: {diff_path.name}")
    return ("FAIL", f"расхождение {frac:.2%} пикселей ({changed})")


def golden_check(page, shot_dir: Path, key: str, update: bool):
    """N1-07. Возвращает (status, detail). update=False и эталон отсутствует → GOLDEN создан."""
    golden_dir = shot_dir.parent / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)
    page.evaluate(STABILIZE_JS)
    page.wait_for_timeout(150)
    cur = shot_dir / f"{key}.png"
    page.screenshot(path=str(cur), full_page=True)
    g = golden_dir / f"{key}.png"
    if update or not g.exists():
        shutil.copy(cur, g)
        return ("GOLDEN", f"эталон сохранён: golden/{key}.png")
    a = Image.open(cur).convert("RGB")
    b = Image.open(g).convert("RGB")
    return _compare_images(a, b, shot_dir / f"{key}.diff.png")


def selftest_golden(shot_dir: Path) -> tuple[str, str]:
    """N6-02: самопроверка прибора. Портит собственный эталон (инвертирует
    блок пикселей), обязан пометить проверку FAIL, затем восстанавливает.

    Без этой проверки отчёт прибора доказательством не считается.
    """
    golden_dir = shot_dir.parent / "golden"
    if not golden_dir.exists():
        return ("SKIP", "каталог эталонов ещё не создан")
    goldens = sorted(golden_dir.glob("*.png"))
    if not goldens:
        return ("SKIP", "нет эталонов для самопроверки")
    g = goldens[0]
    original_bytes = g.read_bytes()  # восстанавливаем БАЙТ-в-байт
    # (Pillow-save перекодирует PNG: те же пиксели, другие байты — md5-контроль
    # эталонов после selftest ловил это расхождение, инцидент 2026-09-11)
    original = Image.open(g).convert("RGB")
    current = original.copy()
    corrupted = original.copy()
    px = corrupted.load()
    w, h = corrupted.size
    for x in range(0, min(200, w), 3):
        for y in range(0, min(200, h), 3):
            r, gr, b = px[x, y]
            px[x, y] = (255 - r, 255 - gr, 255 - b)
    corrupted.save(g)
    try:
        status, detail = _compare_images(current, Image.open(g).convert("RGB"))
    finally:
        g.write_bytes(original_bytes)  # восстанавливаем эталон
    return (status, f"{g.name}: {detail}")


def _check(res: dict, name: str, status: str, detail: str = "") -> None:
    res["checks"].append({"n": name, "s": status, "d": detail})


def lightbox_smoke(page, res) -> None:
    """Состояние «лайтбокс»: открытие, счётчик, след., Escape."""
    if page.query_selector("#slider-main-img") is None:
        _check(res, "N5 lightbox", "SKIP", "нет фотографий")
        return
    page.click("#slider-main-img")
    page.wait_for_timeout(250)
    vis = page.evaluate("() => getComputedStyle(document.getElementById('lightbox')).display")
    if vis == "none":
        _check(res, "N5 lightbox", "FAIL", "не открылся по клику")
        return
    c0 = page.evaluate("() => document.getElementById('lightbox-counter').textContent")
    n = int(c0.split("/")[1].strip())
    page.click("#lightbox-next")
    page.wait_for_timeout(150)
    c1 = page.evaluate("() => document.getElementById('lightbox-counter').textContent")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    closed = page.evaluate("() => getComputedStyle(document.getElementById('lightbox')).display === 'none'")
    if n > 1 and c0 == c1:
        _check(res, "N5 lightbox", "FAIL", f"счётчик не изменился после next: {c0} -> {c1}")
    elif not closed:
        _check(res, "N5 lightbox", "FAIL", "Escape не закрывает")
    else:
        _check(res, "N5 lightbox", "PASS", f"открыт, счётчик {c0}, next={c1}, Escape закрыл")


def modal_smoke(page, res) -> None:
    """N3-04: четыре сценария модального окна + блокировка прокрутки фона."""
    try:
        r = page.evaluate(MODAL_CHECK_JS)
    except Exception as e:
        _check(res, "N3-04 modal", "FAIL", f"ошибка теста: {str(e)[:120]}")
        return
    if not r or "focusInside" not in r:
        why = r.get("skipped") if r else None
        _check(res, "N3-04 modal", "SKIP",
               "модалок на экране нет" if why is True else f"нельзя проверить: {why or 'нет результата'}")
        return
    bad = []
    if not r.get("focusInside"):
        bad.append("фокус не перешёл внутрь окна")
    if not r.get("bgLocked"):
        bad.append("фон прокручивается при открытом окне")
    if r.get("escapeClosed") is not True:
        bad.append("Escape не закрыл")
    if r.get("outsideClosed") is not True:
        bad.append("клик вне окна не закрыл")
    if r.get("buttonClosed") is not True:
        bad.append(f"кнопка не закрыла ({r.get('closeBtn')})")
    if not r.get("focusReturned"):
        bad.append("фокус не вернулся к элементу до открытия")
    if not r.get("bgUnlocked"):
        bad.append("фон не разблокирован после закрытия")
    if bad:
        _check(res, "N3-04 modal", "FAIL", "; ".join(bad))
    else:
        _check(res, "N3-04 modal", "PASS",
               f"кнопка «{r.get('closeBtn')}» / клик вне окна / Escape закрыли; фокус и прокрутка вернулись")


def shipping_smoke(page, context, base: str, lot: str, res) -> None:
    """N3-03: пересчёт итогов без перезагрузки; сверка с API buyer-total."""
    if page.query_selector("[data-testid='btn-add-option']") is None:
        _check(res, "N3-03 shipping", "SKIP", "конструктор не отрисован")
        return
    if not page.evaluate("() => typeof window.Alpine !== 'undefined'"):
        _check(res, "N3-03 shipping", "FAIL", "Alpine не определён, конструктор мёртв")
        return
    rows0 = page.locator("[data-testid='option-row']").count()
    if rows0 > 0:  # сверка отображаемого итога с ответом API, а не с вёрсткой
        dom = page.locator("[data-testid='buyer-total']").first.inner_text()
        # Канон формата V1 (TZ N2 §1): пробелы-разряды, запятая-дробь («5 500,50 Kč»)
        m = re.search(r"(\d(?:[ ]\d{3})*)(?:,(\d+))?", dom)
        try:
            api = context.request.get(f"{base}/api/lots/{lot}/buyer-total", timeout=10000).json()
            dom_val = None
            if m:
                dom_val = float(m.group(1).replace(" ", "")
                                + (f".{m.group(2)}" if m.group(2) else ""))
            if dom_val is not None and abs(dom_val - float(api.get("buyer_total", -1))) > 0.01:
                _check(res, "N3-03 shipping", "FAIL", f"DOM {dom} != API buyer_total={api.get('buyer_total')}")
                return
        except Exception as e:
            _check(res, "N3-03 shipping", "FAIL", f"API buyer-total недоступен: {e}")
            return
    if not page.locator("[data-testid='select-carrier-to-add'] option[value]").count():
        _check(res, "N3-03 shipping", "PASS", "нет доступных перевозчиков для добавления")
        return
    page.click("[data-testid='btn-add-option']")
    page.wait_for_timeout(250)
    rows1 = page.locator("[data-testid='option-row']").count()
    total = page.locator("[data-testid='buyer-total']").last.inner_text() if rows1 > 0 else ""
    if rows1 != rows0 + 1:
        _check(res, "N3-03 shipping", "FAIL", f"добавление опции: строк {rows0} -> {rows1}")
        return
    page.locator("[data-testid='btn-remove-option']").last.click()
    page.wait_for_timeout(250)
    rows2 = page.locator("[data-testid='option-row']").count()
    if rows2 != rows0:
        _check(res, "N3-03 shipping", "FAIL", f"удаление опции: строк {rows1} -> {rows2}")
        return
    _check(res, "N3-03 shipping", "PASS", f"добавление/удаление без перезагрузки, итог «{total}»")


def run(base_url: str, token: str, out_dir: Path, vps: list[str], update: bool, only: list[str],
        no_golden: bool = False, offline: bool = False) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = out_dir / "screenshots"
    shots.mkdir(exist_ok=True)
    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        for vp_name in vps:
            w, h = VIEWPORTS[vp_name]
            mobile = vp_name in ("360", "390")
            ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile,
                                      has_touch=mobile, user_agent=None,
                                      extra_http_headers={"Cookie": f"nextlife_token={token}"})
            external_reqs: list[str] = []
            if offline:
                # N3-01: всё, что не localhost, прерывается и фиксируется.
                def _route(route):
                    host = urlsplit(route.request.url).hostname or ""
                    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
                        route.continue_()
                    else:
                        external_reqs.append(route.request.url)
                        route.abort()
                ctx.route("**/*", _route)
            params = discover_params(ctx, base_url)
            for name, path, group in build_screens(params):
                if only and not any(o in name for o in only):
                    continue
                res = {"screen": f"{name}@{vp_name}", "path": path, "checks": [], "status": "PASS"}
                page = ctx.new_page()
                console, failed = [], []
                page.on("console", lambda m: m.type in ("error", "warning") and console.append(f"[{m.type}] {m.text}"))
                page.on("response", lambda r: r.status >= 400 and failed.append(f"HTTP {r.status} {r.url}"))
                ext_before = len(external_reqs)
                try:
                    resp = page.goto(f"{base_url}{path}", wait_until="domcontentloaded", timeout=15000)
                    status = resp.status if resp else 0
                    if status >= 500:
                        # N4-04: 500 обязан сопровождаться страницей ошибки с
                        # идентификатором; голый 500 — отдельный дефект.
                        body = ""
                        try:
                            body = resp.text()
                        except Exception:
                            pass
                        err_id_m = re.search(r'data-error-id="([^"]+)"', body)
                        if err_id_m:
                            d = f"HTTP {status} (страница ошибки присутствует, id={err_id_m.group(1)})"
                        else:
                            d = f"HTTP {status} (страницы ошибки НЕТ — сырой 500)"
                        res["checks"].append({"n": "HTTP", "s": "FAIL", "d": d})
                        res["status"] = "FAIL"
                    elif status == 404:
                        res["status"] = "BLOCKED"
                        res["checks"].append({"n": "HTTP", "s": "BLOCKED", "d": f"HTTP 404 — маршрут/данные отсутствуют"})
                    else:
                        res["checks"].append({"n": "HTTP", "s": "PASS", "d": f"HTTP {status}"})
                        page.evaluate(WAIT_IMAGES_JS)
                        page.wait_for_timeout(300)
                        m = page.evaluate(MEASURE_JS, w)
                        res["checks"] += [
                            {"n": "N1-01 console", "s": "PASS" if not console else "FAIL", "d": "; ".join(console[:5])},
                            {"n": "N1-01 network", "s": "PASS" if not failed else "FAIL", "d": "; ".join(failed[:5])},
                            {"n": "N1-02 overflow", "s": "PASS" if not m["overflow"] else "FAIL", "d": "; ".join(m["overflow"])},
                            {"n": "N1-03 images", "s": "PASS" if not m["images"] else "FAIL", "d": "; ".join(m["images"])},
                            {"n": "N1-04 taps", "s": "PASS" if not m["taps"] else "FAIL", "d": "; ".join(m["taps"])},
                            {"n": "N1-05 hidden", "s": "PASS" if not (m["hidden"] or m["mods"]) else "FAIL",
                             "d": "; ".join(m["hidden"] + m["mods"])},
                        ]
                        for lib in ("alpine", "htmx"):
                            # N3-02: обе библиотеки подключены в base.html,
                            # поэтому обязаны быть определены на КАЖДОМ экране.
                            if not m[lib]["defined"]:
                                win_name = "Alpine" if lib == "alpine" else "htmx"
                                res["checks"].append(
                                    {"n": f"N1-06 {lib}", "s": "FAIL",
                                     "d": f"window.{win_name} не определён (библиотека обязана грузиться на каждом экране)"})
                            else:
                                res["checks"].append(
                                    {"n": f"N1-06 {lib}", "s": "PASS",
                                     "d": "определён" + (" (директивы используются)" if m[lib]["used"] else "")})
                        if no_golden:
                            res["checks"].append({"n": "N1-07 golden", "s": "SKIP", "d": "--no-golden (эталоны не применяются к этому бильду)"})
                        else:
                            gstat, gdet = golden_check(page, shots, re.sub(r"[^A-Za-z0-9]+", "_", name) + f"_{vp_name}", update)
                            res["checks"].append({"n": "N1-07 golden", "s": gstat, "d": gdet})
                        if m.get("has_modal"):
                            # N3-04: четыре сценария модалки — ПОСЛЕ скриншота
                            # (скриншот обязан быть в исходном состоянии, модалка скрыта)
                            modal_smoke(page, res)
                        if "lightbox" in name:
                            lightbox_smoke(page, res)
                        if "shipping[" in name:
                            shipping_smoke(page, ctx, base_url, name.split("[")[1].split("]")[0], res)
                        if name == "items":
                            bad = []
                            for c in params["codes"]:
                                resp = _req_get(ctx, f"{base_url}/ui/items/{c}")
                                if resp is None:
                                    bad.append(f"#{c}: сетевая ошибка (2 попытки)")
                                elif resp.status >= 500:
                                    bad.append(f"#{c}: HTTP {resp.status}")
                            res["checks"].append({"n": "N1-01 detail-sweep", "s": "PASS" if not bad else "FAIL",
                                                  "d": "; ".join(bad) if bad else f"все {len(params['codes'])} карточки — 200"})
                        if name == "wizard" and params["codes"]:
                            # N5: прохождение всех пяти шагов мастера (GET).
                            lot = (params["photo_codes"] or params["codes"])[0]
                            bad = []
                            for step in range(1, 6):
                                resp = _req_get(ctx, f"{base_url}/ui/wizard/{lot}?step={step}")
                                if resp is None:
                                    bad.append(f"step {step}: сетевая ошибка")
                                elif resp.status != 200:
                                    bad.append(f"step {step}: HTTP {resp.status}")
                            res["checks"].append({"n": "N5 wizard-walk", "s": "PASS" if not bad else "FAIL",
                                                  "d": "; ".join(bad) if bad else f"5/5 шагов отрендерились (лот {lot})"})
                except Exception as e:
                    res["checks"].append({"n": "LOAD", "s": "FAIL", "d": str(e)[:160]})
                    res["status"] = "FAIL"
                if offline:
                    ext = external_reqs[ext_before:]
                    res["checks"].append({"n": "N3-01 offline", "s": "PASS" if not ext else "FAIL",
                                          "d": "; ".join(ext[:5]) if ext else "внешних запросов нет"})
                if res["status"] != "BLOCKED" and any(c["s"] == "FAIL" for c in res["checks"]):
                    res["status"] = "FAIL"
                res["console"] = console
                res["network"] = failed
                results.append(res)
                page.close()
            ctx.close()
        browser.close()

    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_blocked = sum(1 for r in results if r["status"] == "BLOCKED")
    lines = ["# UI Audit Report — NextLife", f"Run: {datetime.now().isoformat()}", f"URL: {base_url}",
             "", "## Итог", f"- FAIL: {n_fail}", f"- BLOCKED: {n_blocked}", f"- всего экранов×вьюпортов: {len(results)}", ""]
    for r in results:
        icon = {"FAIL": "❌", "BLOCKED": "⛔", "PASS": "✅"}[r["status"]]
        lines.append(f"## {icon} `{r['screen']}` ({r['path']})")
        for c in r["checks"]:
            ci = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "⛔", "SKIP": "⏭️", "GOLDEN": "📸"}[c["s"]]
            lines.append(f"- {ci} **{c['n']}** {c['s']}" + (f" — {c['d'][:200]}" if c["d"] else ""))
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Report: {out_dir / 'report.md'} | FAIL={n_fail} BLOCKED={n_blocked} total={len(results)}")
    return 1 if n_fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="NextLife UI-прибор (N1)")
    ap.add_argument("--url", default=os.environ.get("NEXTLIFE_BASE_URL", "http://localhost:8081"))
    ap.add_argument("--token", default=os.environ.get("NEXTLIFE_WEB_TOKEN", ""))
    ap.add_argument("--out", default="ui-audit/run")
    ap.add_argument("--viewports", default="360,390,768,1280")
    ap.add_argument("--update-golden", action="store_true", help="пересоздать эталоны")
    ap.add_argument("--only", default="", help="через запятую: часть имён экранов")
    ap.add_argument("--no-golden", action="store_true", help="пропуск N1-07 (верификационные прогоны)")
    ap.add_argument("--offline", action="store_true",
                    help="N3-01: прерывать все неблокальные (внешние) запросы и проверять, что их нет")
    ap.add_argument("--selftest", action="store_true",
                    help="N6-02: самопроверка — прибор портит собственный эталон и обязан дать FAIL")
    a = ap.parse_args()

    if a.selftest:
        # Н6-02: standalone. Требует наличие эталонов в <out>/golden.
        out_dir = Path(a.out)
        shots = out_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        status, detail = selftest_golden(shots)
        print(f"SELFTEST N6-02: {status} — {detail}")
        if status == "SKIP":
            print("ERROR: эталоны отсутствуют — сначала выполните прогон с эталонами", file=sys.stderr)
            return 2
        # Самопроверка работает, если прибор ЗАФИКСИРОВАЛ порчу (FAIL).
        if status == "FAIL":
            print("SELFTEST OK: порча эталона зафиксирована как FAIL (прибор работоспособен).")
            return 0
        print("ERROR: прибор не зафиксировал порчу эталона — дефект прибора.", file=sys.stderr)
        return 1

    if not a.token:
        print("ERROR: --token / NEXTLIFE_WEB_TOKEN обязателен", file=sys.stderr)
        return 2
    vps = [v.strip() for v in a.viewports.split(",") if v.strip() in VIEWPORTS]
    if not vps:
        print("ERROR: --viewports пуст (допустимо: 360,390,768,1280)", file=sys.stderr)
        return 2
    return run(a.url.rstrip("/"), a.token, Path(a.out), vps, a.update_golden,
               [o.strip() for o in a.only.split(",") if o.strip()],
               no_golden=a.no_golden, offline=a.offline)


if __name__ == "__main__":
    sys.exit(main())
