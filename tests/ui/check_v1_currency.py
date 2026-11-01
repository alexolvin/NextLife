"""Очередь V1 (валюта) — регрессионные проверки TZ-NextLife-UI-N2.

P-E01  ни на одном экране лота и сделки нет кода главной валюты (EUR);
P-E02  цена, введённая в мастере под подписью «Kč», отображается как Kč
       в карточке, черновике, предпросмотре и конструкторе (+ сверка с БД);
P-E02b попытка подставить price_base в исходящий шаблон вызывает исключение;
P-E16  лот без цены показывает «цена не задана» и не показывает валюту.
"""

from __future__ import annotations

import urllib.request

from scripts.checklib import CheckFail

from nextlife.core.exceptions import SecretResolutionError
from nextlife.core.secrets import resolve_secret

try:
    TOKEN = resolve_secret("NEXTLIFE_WEB_TOKEN")
except SecretResolutionError:
    raise SystemExit(
        "NEXTLIFE_WEB_TOKEN не задан в окружении процесса: задайте токен "
        "веб-интерфейса (auth.web_token_secret_ref)"
    ) from None

EXPECTED_PRICE = "12 345 Kč"  # формат ТЗ §1.4 для 12345 CZK (задан спецификацией, не модулем)


def _price_el(ctx) -> str | None:
    return ctx.page.evaluate(
        "() => { const e = document.querySelector('.price'); return e ? e.innerText.trim() : null; }"
    )


def p_e01(ctx) -> None:
    """Kод главной валюты (EUR) не присутствует на экранах лота и сделки."""
    rows = ctx.db("SELECT id FROM deal WHERE status='ACTIVE' ORDER BY created_at LIMIT 1")
    ctx.expect(bool(rows), "в копии БД нет активной сделки — фикстура сломана")
    active_deal = rows[0][0]
    screens = [
        "/ui/items",
        "/ui/items/747",
        "/ui/items/348",
        "/ui/draft/747",
        "/ui/draft/348",
        "/ui/items/747/preview/bazos_cz",
        "/ui/items/348/preview/bazos_cz",
        "/ui/lots/747/shipping",
        "/ui/lots/747/edit",
        f"/ui/deals/{active_deal}",
        "/ui/deals/deal-348-1",
    ]
    for s in screens:
        text = ctx.nav(s)
        ctx.no_errors()
        ctx.text_not_has(text, "EUR", f"экран {s}")
    # эталоны ключевых экранов (4 вьюпорта)
    golden_screens = [
        ("feed", "/ui/items"),
        ("item747", "/ui/items/747"),
        ("draft747", "/ui/draft/747"),
        ("preview747", "/ui/items/747/preview/bazos_cz"),
        ("deal_active", f"/ui/deals/{active_deal}"),
    ]
    for key, url in golden_screens:
        for vp, st, det in ctx.golden(key, url):
            if st == "FAIL":
                ctx.expect(False, f"golden {key}/{vp}: {det}")
    ctx.shot("e01_last_screen")


def p_e02(ctx) -> None:
    """Сценарий: мастер -> цена 12345 под подписью «Kč» -> везде «12 345 Kč»."""
    code = None
    ctx.nav("/ui/wizard")
    ctx.no_errors()
    ctx.page.fill("#wz-title", "UI Check V1 Price")
    ctx.page.select_option("#wz-category", "electronics")
    ctx.page.fill("#wz-notes", "проверка валюты (V1, копия БД)")
    ctx.page.click("button[type=submit]")
    ctx.page.wait_for_load_state("load")
    ctx.page.wait_for_timeout(1200)
    code = ctx.page.url.split("/wizard/")[1].split("?")[0]
    ctx.expect(code.isdigit() and len(code) == 3, f"не получен код лота: {ctx.page.url}")

    # шаг 2 (без фото) -> шаг 3
    ctx.page.click("button[type=submit]")
    ctx.page.wait_for_load_state("load")
    ctx.page.wait_for_timeout(1000)
    ctx.page.select_option("#wz-condition", "B")
    ctx.page.click("button[type=submit]")
    ctx.page.wait_for_load_state("load")
    ctx.page.wait_for_timeout(1000)
    # шаг 4: цена
    ctx.page.fill("#wz-price", "12345")
    ctx.page.click("button[type=submit]")
    ctx.page.wait_for_load_state("load")
    ctx.page.wait_for_timeout(1000)
    # шаг 5: завершить
    ctx.page.select_option("#wz-shipping-policy", "BUYER_PAYS")
    ctx.page.click("button[type=submit]")
    ctx.page.wait_for_load_state("load")
    ctx.page.wait_for_timeout(1500)

    try:
        # карточка
        ctx.nav(f"/ui/items/{code}")
        ctx.no_errors()
        price_txt = _price_el(ctx)
        ctx.shot("card")
        ctx.expect(price_txt == EXPECTED_PRICE,
                   f"карточка: {price_txt!r} != {EXPECTED_PRICE!r}")
        for vp, st, det in ctx.golden("wizard_price_card", f"/ui/items/{code}"):
            if st == "FAIL":
                ctx.expect(False, f"golden wizard_price_card/{vp}: {det}")
        # черновик
        ctx.nav(f"/ui/draft/{code}")
        ctx.no_errors()
        ctx.expect(_price_el(ctx) == EXPECTED_PRICE,
                   f"черновик: {_price_el(ctx)!r} != {EXPECTED_PRICE!r}")
        ctx.shot("draft")
        # предпросмотр площадки
        text = ctx.nav(f"/ui/items/{code}/preview/bazos_cz")
        ctx.no_errors()
        ctx.text_has(text, EXPECTED_PRICE, "предпросмотр Bazoš")
        ctx.shot("preview")
        # конструктор доставки
        text = ctx.nav(f"/ui/lots/{code}/shipping")
        ctx.no_errors()
        ctx.text_has(text, EXPECTED_PRICE, "конструктор доставки")
        ctx.shot("shipping")
        # сверка с БД (ТЗ §3.2.1): экран == БД в ожидаемом формате
        rows = ctx.db("SELECT price_gross, currency FROM lot WHERE item_id=%s", (code,))
        ctx.expect(bool(rows), "лот не найден в БД")
        ctx.expect(rows[0][1] == "CZK",
                   f"lot.currency в БД = {rows[0][1]!r} (ожидалось 'CZK')")
        ctx.expect(str(rows[0][0]) == "12345.00",
                   f"lot.price_gross в БД = {rows[0][0]!r} (ожидалось 12345.00)")
    finally:
        if not (code and code.isdigit()):
            return
        # очистка (копия БД — разрешено): soft-delete через API + жёсткое удаление строк
        req = urllib.request.Request(
            f"{ctx.base}/api/lots/{code}", method="DELETE",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:  # noqa: BLE001
            pass
        for sql, params in [
            ("DELETE FROM item_photo WHERE item_code=%s", (code,)),
            ("DELETE FROM media_original WHERE item_code=%s", (code,)),
            ("DELETE FROM lot WHERE item_id=%s", (code,)),
            ("DELETE FROM item WHERE code=%s", (code,)),
        ]:
            ctx.db(sql, params)


def p_e02b(ctx) -> None:
    """Барьер сериализации: price_base в исходящих данных -> исключение."""
    try:
        from nextlife.core.internal_fields import (
            InternalFieldLeakError,
            assert_outgoing_clean,
        )
    except Exception as exc:  # noqa: BLE001
        raise CheckFail(f"барьер внутреннего поля отсутствует: {exc}") from exc
    try:
        assert_outgoing_clean({"price": "12345", "price_base": "300.00"})
    except InternalFieldLeakError:
        pass
    else:
        raise CheckFail("assert_outgoing_clean не вызвал исключение при price_base")
    # чистый прогон проходит
    assert_outgoing_clean({"price": "12345", "currency": "CZK"})


def p_e16(ctx) -> None:
    """Лот без цены: «цена не задана», без кода валюты (фикстура — лот 417 в копии)."""
    orig_lot = ctx.db("SELECT price_gross FROM lot WHERE item_id='417'")
    orig_item = ctx.db("SELECT target_price, target_currency FROM item WHERE code='417'")
    ctx.expect(bool(orig_lot) and bool(orig_item), "лот 417 не найден в копии БД")
    try:
        ctx.db("UPDATE lot SET price_gross=NULL WHERE item_id='417'")
        ctx.db("UPDATE item SET target_price=NULL, target_currency=NULL WHERE code='417'")
        ctx.nav("/ui/items")
        ctx.no_errors()
        price_txt = ctx.page.evaluate(
            "() => { const e = document.querySelector('#item-417 .price'); "
            "return e ? e.innerText.trim() : null; }"
        )
        ctx.expect(price_txt is not None, "карточка #417 не найдена в ленте")
        ctx.shot("feed_no_price")
        ctx.expect("цена не задана" in price_txt,
                   f"текст цены: {price_txt!r} (ожидалось «цена не задана»)")
        ctx.expect("EUR" not in price_txt and "CZK" not in price_txt,
                   f"валюта показана у цены без суммы: {price_txt!r}")
    finally:
        ctx.db("UPDATE lot SET price_gross=%s WHERE item_id='417'", (orig_lot[0][0],))
        ctx.db("UPDATE item SET target_price=%s, target_currency=%s WHERE code='417'",
               (orig_item[0][0], orig_item[0][1]))


CHECKS = [
    ("P-E01", "главная валюта (EUR) отсутствует на экранах лота и сделки", p_e01),
    ("P-E02", "цена мастера «Kč» отображается как «12 345 Kč» везде (карточка/черновик/превью/доставка) + сверка с БД", p_e02),
    ("P-E02b", "price_base не проходит барьер сериализации исходящих данных", p_e02b),
    ("P-E16", "лот без цены: «цена не задана», без валюты", p_e16),
]
