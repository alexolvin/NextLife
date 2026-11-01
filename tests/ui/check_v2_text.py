"""TZ-NextLife-UI-N2 V2 (Текст) — проверки P-E03, P-E06, P-E12, P-E18.

P-E03  финальный текст для чешской площадки проходит проверку языка;
       русский текст даёт блокирующее предупреждение на экране черновика
       и блокирует утверждение;
P-E06  предпросмотр площадки содержит финальный публикуемый текст
       (не устаревший attrs.description);
P-E12  правка текста в форме лота изменяет именно тот текст, который
       уйдёт в публикацию (единый источник правды);
P-E18  название шага мастера соответствует его содержимому
       («разведка» не пишется на шаге без разведки).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from nextlife.core.exceptions import SecretResolutionError
from nextlife.core.secrets import resolve_secret

FIXTURE = "106"  # DRAFT-лот в копии БД; не используется проверками V1

try:
    TOKEN = resolve_secret("NEXTLIFE_WEB_TOKEN")
except SecretResolutionError:
    raise SystemExit(
        "NEXTLIFE_WEB_TOKEN не задан в окружении процесса: задайте токен "
        "веб-интерфейса (auth.web_token_secret_ref)"
    ) from None


def _post(ctx, url: str, data: dict) -> int:
    """POST формы (urllib, без JS). Возвращает итоговый HTTP-статус
    (303/302 следуют редиректу → 200) либо 4xx при блокировке."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        ctx.base + url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _save_item(ctx, code: str) -> dict:
    row = ctx.db("SELECT notes, attrs, status FROM item WHERE code=%s", (code,))
    ctx.expect(bool(row), f"предмет {code} не найден в копии БД")
    lot = ctx.db("SELECT status FROM lot WHERE item_id=%s", (code,))
    return {
        "notes": row[0][0],
        "attrs": row[0][1] if row[0][1] is not None else {},
        "status": row[0][2],
        "lot_status": lot[0][0] if lot else None,
    }


def _restore_item(ctx, code: str, orig: dict) -> None:
    ctx.db(
        "UPDATE item SET notes=%s, attrs=CAST(%s AS jsonb), status=%s WHERE code=%s",
        (orig["notes"], json.dumps(orig["attrs"]), orig["status"], code),
    )
    if orig["lot_status"] is not None:
        ctx.db("UPDATE lot SET status=%s WHERE item_id=%s", (orig["lot_status"], code))
    ctx.db("DELETE FROM approval WHERE item_code=%s", (code,))


def _set_final_text(ctx, code: str, text: str) -> None:
    ctx.db(
        "UPDATE item SET attrs=jsonb_set(COALESCE(attrs, '{}'::jsonb), "
        "'{final_text}', to_jsonb(%s::text)) WHERE code=%s",
        (text, code),
    )


def _set_notes(ctx, code: str, text: str) -> None:
    ctx.db("UPDATE item SET notes=%s WHERE code=%s", (text, code))


def _draft_hash(ctx, code: str) -> str:
    ctx.nav(f"/ui/draft/{code}")
    # TZ-NextLife-UI-N2 V4 (P-E13): хэш — внутри свёрнутого служебного
    # блока; читаем через textContent (значение в DOM, невидимость
    # пользователю — отдельная проверка P-E13 по body.innerText).
    info = ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"hash-info\"]'); "
        "return e ? e.textContent : ''; }"
    )
    m = re.search(r"[a-f0-9]{64}", info or "")
    ctx.expect(bool(m), f"payload_hash не найден на экране черновика: {info!r}")
    return m.group(0)


def p_e03(ctx) -> None:
    """RU-текст на cz-площадке → блокирующее предупреждение + блок утверждения."""
    code = FIXTURE
    orig = _save_item(ctx, code)
    try:
        # 1) Русский текст: предупреждение видно, кнопка заблокирована,
        #    серверное утверждение с верным хэшем → 400.
        _set_notes(ctx, code, "")
        _set_final_text(ctx, code, "V2-ПРОВЕРКА-ЯЗЫКА: этот текст написан на русском")
        ctx.nav(f"/ui/draft/{code}")
        ctx.no_errors()
        warn = ctx.page.query_selector('[data-testid="language-warning"]')
        ctx.expect(warn is not None,
                   "RU-текст: блокирующее предупреждение языка не показано на экране черновика")
        ctx.shot("e03_ru_warning")
        btn = ctx.page.query_selector("#btn-approve")
        ctx.expect(btn is not None, "кнопка утверждения не найдена")
        ctx.expect(btn.get_attribute("disabled") is not None,
                   "RU-текст: кнопка утверждения не заблокирована")
        h = _draft_hash(ctx, code)
        status = _post(ctx, f"/ui/draft/{code}/approve", {"payload_hash": h})
        ctx.expect(status == 400,
                   f"RU-текст: серверное утверждение вернуло {status}, ожидался 400 (блок)")
        # 2) Чешский текст: предупреждения нет, кнопка активна.
        _set_final_text(ctx, code, "V2-OBNOUZENY-TEXT: prodám ve velmi dobrém stavu")
        ctx.nav(f"/ui/draft/{code}")
        ctx.no_errors()
        warn2 = ctx.page.query_selector('[data-testid="language-warning"]')
        ctx.expect(warn2 is None, "CZ-текст: предупреждение о языке показано ложно")
        btn2 = ctx.page.query_selector("#btn-approve")
        ctx.expect(btn2 is not None and btn2.get_attribute("disabled") is None,
                   "CZ-текст: кнопка утверждения заблокирована ложно")
        ctx.shot("e03_cz_ok")
    finally:
        _restore_item(ctx, code, orig)


def p_e06(ctx) -> None:
    """Предпросмотр площадки = финальный публикуемый текст (не attrs.description)."""
    code = FIXTURE
    orig = _save_item(ctx, code)
    try:
        ctx.db(
            "UPDATE item SET attrs=jsonb_set(jsonb_set(COALESCE(attrs, '{}'::jsonb), "
            "'{final_text}', to_jsonb(%s::text)), '{description}', to_jsonb(%s::text)) "
            "WHERE code=%s",
            ("V2-PREVIEW-UNIQUE-TEXT-9C41", "LEGACY-DESC-SHOULD-NOT-APPEAR", code),
        )
        text = ctx.nav(f"/ui/items/{code}/preview/bazos_cz")
        ctx.no_errors()
        ctx.text_has(text, "V2-PREVIEW-UNIQUE-TEXT-9C41",
                     "предпросмотр Bazoš: финальный публикуемый текст")
        ctx.text_not_has(text, "LEGACY-DESC-SHOULD-NOT-APPEAR",
                         "предпросмотр Bazoš: устаревший attrs.description")
        ctx.shot("e06_preview_final_text")
    finally:
        _restore_item(ctx, code, orig)


def p_e12(ctx) -> None:
    """Правка текста в форме лота меняет именно уходящий в публикацию текст."""
    code = FIXTURE
    orig = _save_item(ctx, code)
    try:
        # Фикстура: у предмета есть внутренние заметки (маскируют description
        # в «старой» модели двух источников правды).
        _set_notes(ctx, code, "V2-WORKING-NOTES-INTERNAL")
        title = ctx.db("SELECT title FROM item WHERE code=%s", (code,))[0][0]
        # 1) Форма лота и черновик показывают ОДИН и тот же уходящий текст.
        ctx.nav(f"/ui/draft/{code}")
        ctx.no_errors()
        body_draft = ctx.page.evaluate(
            "() => document.getElementById('listing-text-input').value"
        )
        ctx.nav(f"/ui/lots/{code}/edit")
        ctx.no_errors()
        body_edit = ctx.page.evaluate(
            "() => document.getElementById('lot-description').value"
        )
        ctx.expect(body_edit == body_draft,
                   f"форма лота: текст формы ({(body_edit or '')[:50]!r}) "
                   f"не совпадает с уходящим текстом черновика ({(body_draft or '')[:50]!r})")
        # 2) Правка через форму лота → уходящий текст изменён.
        new_text = "V2-LOT-FORM-OUTGOING-TEXT-B7E2"
        st = _post(ctx, f"/ui/lots/{code}/edit", {"title": title, "description": new_text})
        ctx.expect(st in (200, 303), f"POST /ui/lots/{code}/edit вернул {st}")
        ctx.nav(f"/ui/draft/{code}")
        ctx.no_errors()
        body_after = ctx.page.evaluate(
            "() => document.getElementById('listing-text-input').value"
        )
        ctx.expect(body_after == new_text,
                   f"черновик после правки формы лота: {(body_after or '')[:80]!r} != {new_text!r}")
        ctx.shot("e12_edit_updates_outgoing")
    finally:
        _restore_item(ctx, code, orig)


def p_e18(ctx) -> None:
    """Название шага мастера соответствует содержимому (шаг 4, лот 747 — без создания лота)."""
    code = "747"
    ctx.nav(f"/ui/wizard/{code}?step=4")
    ctx.no_errors()
    h3 = ctx.page.evaluate(
        "() => { const e = document.querySelector('h3'); return e ? e.innerText.trim() : ''; }"
    )
    ctx.expect(h3.startswith("Шаг 4"), f"заголовок шага 4 не найден: {h3!r}")
    price_field = ctx.page.query_selector("#wz-price")
    ctx.expect(price_field is not None, "шаг 4: поле цены отсутствует")
    has_research = ctx.page.evaluate(
        "() => !!document.querySelector('[data-testid*=\"research\"], #wz-research, .research-block')"
    )
    if has_research:
        ctx.expect("разведк" in h3.lower(),
                   f"в шаге есть блок разведки, но заголовок её не называет: {h3!r}")
    else:
        ctx.expect("разведк" not in h3.lower(),
                   f"заголовок шага называет «разведку», но блока разведки нет: {h3!r}")
    ctx.shot("e18_wizard_step4")


CHECKS = [
    ("P-E03", "финальный текст для cz-площадки проходит проверку языка; RU-текст даёт блокирующее предупреждение", p_e03),
    ("P-E06", "предпросмотр площадки содержит финальный публикуемый текст (не attrs.description)", p_e06),
    ("P-E12", "правка текста в форме лота изменяет именно тот текст, который уйдёт в публикацию", p_e12),
    ("P-E18", "название шага мастера соответствует его содержимому («разведка» без разведки)", p_e18),
]
