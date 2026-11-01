"""TZ-NextLife-UI-N2 V4 (Данные и словари) — проверки P-E07, P-E09, P-E10, P-E11, P-E13, P-E14, P-E15, P-E19.

P-E07   блок «Дефекты» содержит только дефекты; позитивные формулировки —
       отдельным блоком «Особенности», не в красном;
P-E09   экран сделки не содержит строки «N/A»; при неполных связях —
       деградированный, но осмысленный текст;
P-E10   единый словарь политики доставки (мастер/правка/конструктор),
       значение мастера сохраняется и присутствует везде; в БД без INCLUDED
       и с CHECK-ограничением;
P-E11   лента в фильтре «Все» не содержит отменённых и удалённых лотов;
P-E13   хэши и строки аудита — внутри свёрнутого служебного блока;
P-E14   заголовок страницы без опечатки («рекомендации»);
P-E15   статусы лотов (лента/карточка/правка/сделка) выводятся по-русски;
P-E19   справочник категорий — не менее 5 позиций, все доступны в мастере,
       заголовки по-русски, используемые категории объявлены.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import yaml

from nextlife.core.exceptions import SecretResolutionError
from nextlife.core.secrets import resolve_secret

CATEGORY_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "categories"

try:
    TOKEN = resolve_secret("NEXTLIFE_WEB_TOKEN")
except SecretResolutionError:
    raise SystemExit(
        "NEXTLIFE_WEB_TOKEN не задан в окружении процесса: задайте токен "
        "веб-интерфейса (auth.web_token_secret_ref)"
    ) from None

# Спецификация (ТЗ §4 V4): единый словарь политики доставки после миграции
# INCLUDED -> SELLER_PAYS.
POLICY_DICT = ("BUYER_PAYS", "SELLER_PAYS", "PICKUP_ONLY")

# Спецификация: русские названия статусов для экранов.
STATUS_RU = {
    "DRAFT": "Черновик",
    "AWAITING_APPROVAL": "Ожидает утверждения",
    "APPROVED": "Утверждён",
    "PUBLISHED": "Опубликован",
    "ACTIVE": "Активен",
}
DEAL_STATUS_RU = {
    "ACTIVE": "Активная",
    "COMPLETED": "Завершена",
}


def _category_dict() -> dict[str, str]:
    """Справочник категорий = config/categories/*.yaml (ключ -> заголовок)."""
    out: dict[str, str] = {}
    for f in sorted(CATEGORY_DIR.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if data.get("key"):
            out[str(data["key"])] = str(data.get("title") or data["key"])
    return out


def _defects_box(ctx) -> str:
    return ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"item-defects\"]');"
        " return e ? e.innerText : ''; }"
    )


def p_e07(ctx) -> None:
    """Позитивные формулировки — не дефекты: красный блок только с дефектами."""
    # 747: единственный «дефект» — «Идеальное состояние, без следов» (особенность).
    ctx.nav("/ui/items/747")
    ctx.no_errors()
    box = _defects_box(ctx)
    ctx.expect("идеальн" not in box.lower(),
               f"блок «Дефекты» у 747 содержит позитивную формулировку: {box[:200]!r}")
    feats = ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"item-features\"]');"
        " return e ? e.innerText : ''; }"
    )
    ctx.expect("идеальное состояние" in feats.lower(),
               "особенность 747 «Идеальное состояние, без следов» не выведена "
               "в блоке «Особенности»")
    # 777: реальный дефект остаётся в красном блоке.
    ctx.nav("/ui/items/777")
    ctx.no_errors()
    box777 = _defects_box(ctx)
    ctx.expect("микропотертость" in box777.lower(),
               f"реальный дефект 777 отсутствует в блоке «Дефекты»: {box777[:200]!r}")
    # 348: микропылинки — дефект (с пояснением), не особенность.
    ctx.nav("/ui/items/348")
    ctx.no_errors()
    box348 = _defects_box(ctx)
    ctx.expect("микропылинки" in box348.lower(),
               f"дефект 348 отсутствует в блоке «Дефекты»: {box348[:200]!r}")
    ctx.shot("e07_defects_split")


def p_e09(ctx) -> None:
    """Экран сделки без «N/A»: деградированный, но осмысленный текст."""
    rows = ctx.db(
        "SELECT id FROM deal WHERE lot_id IS NULL AND party_id IS NULL "
        "ORDER BY created_at LIMIT 1"
    )
    ctx.expect(bool(rows), "в копии БД нет сделки без лота и контрагента — фикстура сломана")
    text = ctx.nav(f"/ui/deals/{rows[0][0]}")
    ctx.no_errors()
    ctx.expect("n/a" not in text.lower(),
               f"экран сделки без лота: строка N/A присутствует: {text[:300]!r}")
    ctx.text_has(text, "Предмет не привязан к сделке", "деградированный текст при отсутствии лота")
    ctx.text_has(text, "Контакт не указан", "деградированный текст при отсутствии контакта контрагента")
    # Активная сделка с лотом, но без контрагента: контакт деградирует, не N/A.
    rows2 = ctx.db(
        "SELECT id FROM deal WHERE status='ACTIVE' AND party_id IS NULL ORDER BY created_at LIMIT 1"
    )
    ctx.expect(bool(rows2), "в копии БД нет активной сделки без контрагента — фикстура сломана")
    text2 = ctx.nav(f"/ui/deals/{rows2[0][0]}")
    ctx.no_errors()
    ctx.expect("n/a" not in text2.lower(),
               f"активная сделка: строка N/A присутствует: {text2[:300]!r}")
    ctx.text_has(text2, "Контакт не указан", "контакт контрагента на активной сделке")
    ctx.shot("e09_deal_degraded")


def p_e10(ctx) -> None:
    """Единый словарь политики доставки: мастер/правка/конструктор + БД."""
    code: str | None = None
    try:
        # Лот для сценария: мастер, шаг 1.
        ctx.nav("/ui/wizard")
        ctx.no_errors()
        ctx.page.fill("#wz-title", "UI Check V4 Policy")
        ctx.page.select_option("#wz-category", "electronics")
        ctx.page.click("button[type=submit]")
        ctx.page.wait_for_load_state("load")
        ctx.page.wait_for_timeout(1200)
        code = ctx.page.url.split("/wizard/")[1].split("?")[0]
        ctx.expect(code.isdigit() and len(code) == 3, f"не получен код лота: {ctx.page.url}")

        # 1) Мастер, шаг 5: единый словарь (SELLER_PAYS есть, INCLUDED нет).
        ctx.nav(f"/ui/wizard/{code}?step=5")
        ctx.no_errors()
        opts = ctx.page.evaluate(
            "() => Array.from(document.querySelectorAll('#wz-shipping-policy option')).map(o => o.value)"
        )
        ctx.expect("SELLER_PAYS" in opts,
                   f"мастер шаг 5: опции {opts} — нет SELLER_PAYS (единый словарь политики доставки)")
        ctx.expect("INCLUDED" not in opts,
                   f"мастер шаг 5: устаревшее значение INCLUDED в опциях: {opts}")
        # Форма правки — тот же словарь.
        ctx.nav(f"/ui/lots/{code}/edit")
        ctx.no_errors()
        opts_edit = ctx.page.evaluate(
            "() => Array.from(document.querySelectorAll('#lot-shipping-policy option')).map(o => o.value)"
        )
        ctx.expect("SELLER_PAYS" in opts_edit and "INCLUDED" not in opts_edit,
                   f"форма правки: опции {opts_edit}")
        # Конструктор доставки — тот же словарь.
        ctx.nav(f"/ui/lots/{code}/shipping")
        ctx.no_errors()
        opts_ship = ctx.page.evaluate(
            "() => Array.from(document.querySelectorAll('#select-shipping-policy option')).map(o => o.value)"
        )
        ctx.expect("SELLER_PAYS" in opts_ship and "INCLUDED" not in opts_ship,
                   f"конструктор доставки: опции {opts_ship}")

        # 2) Круговой сценарий: SELLER_PAYS сохранён мастером -> БД, правка, конструктор.
        ctx.nav(f"/ui/wizard/{code}?step=5")
        ctx.no_errors()
        ctx.page.select_option("#wz-shipping-policy", "SELLER_PAYS")
        ctx.page.click("button[type=submit]")
        ctx.page.wait_for_load_state("load")
        ctx.page.wait_for_timeout(1000)
        db_policy = ctx.db("SELECT shipping_policy FROM lot WHERE item_id=%s", (code,))
        ctx.expect(bool(db_policy) and db_policy[0][0] == "SELLER_PAYS",
                   f"политика в БД: {db_policy!r} (ожидалось SELLER_PAYS)")
        ctx.nav(f"/ui/lots/{code}/edit")
        ctx.no_errors()
        selected = ctx.page.evaluate("() => document.querySelector('#lot-shipping-policy').value")
        ctx.expect(selected == "SELLER_PAYS",
                   f"форма правки: сохранённое значение не выбрано: {selected!r}")
        ctx.nav(f"/ui/lots/{code}/shipping")
        ctx.no_errors()
        src = ctx.page.content()
        ctx.expect("SELLER_PAYS" in src,
                   "конструктор доставки: сохранённая политика SELLER_PAYS не в первичных данных экрана")

        # 3) БД: без INCLUDED; CHECK-ограничение отклоняет INCLUDED.
        cnt = ctx.db("SELECT count(*) FROM lot WHERE shipping_policy='INCLUDED'")[0][0]
        ctx.expect(cnt == 0, f"строк с shipping_policy='INCLUDED' в БД: {cnt}")
        rejected = False
        try:
            ctx.db("UPDATE lot SET shipping_policy='INCLUDED' WHERE code=%s", (code,))
        except Exception:  # noqa: BLE001
            rejected = True
            ctx._conn.rollback()  # связь в состоянии отменённой транзакции
        if not rejected:
            # RED: БД приняла INCLUDED — восстанавливаем значение.
            ctx.db("UPDATE lot SET shipping_policy='SELLER_PAYS' WHERE code=%s", (code,))
        ctx.expect(rejected, "БД приняла shipping_policy='INCLUDED' — CHECK-ограничение отсутствует")
    finally:
        if code and code.isdigit():
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
                ("DELETE FROM lot WHERE item_id=%s OR code=%s", (code, code)),
                ("DELETE FROM item WHERE code=%s", (code,)),
            ]:
                ctx.db(sql, params)


# (код фикстуры, статус предмета, статус лота): 900 — результат мягкого
# удаления последнего лота (item CANCELLED + lot DELETED), 901 — контроль.
FIXTURES = (
    ("900", "CANCELLED", "DELETED"),
    ("901", "DRAFT", "DRAFT"),
)


def p_e11(ctx) -> None:
    """Лента «Все»: без отменённых и удалённых лотов."""
    try:
        for code, istatus, lotstatus in FIXTURES:
            ctx.db("DELETE FROM item WHERE code=%s", (code,))
            ctx.db("DELETE FROM lot WHERE item_id=%s OR code=%s", (code, code))
            ctx.db(
                "INSERT INTO item (code, kind, title, category_key, attrs, condition_key, "
                "defects, status, created_at, updated_at) "
                "VALUES (%s, 'SELL', %s, 'electronics', '{}'::jsonb, 'used', '[]'::jsonb, %s, NOW(), NOW())",
                (code, f"UI Check V4 Feed {code}", istatus),
            )
            ctx.db(
                "INSERT INTO lot (id, code, item_id, status, currency, shipping_policy, "
                "created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, 'CZK', 'BUYER_PAYS', NOW(), NOW())",
                (f"v4-{code}", code, code, lotstatus),
            )
        ctx.nav("/ui/items")
        ctx.no_errors()
        card900 = ctx.page.evaluate(
            "() => { const e = document.querySelector('#item-900'); return e ? e.innerText : null; }"
        )
        ctx.expect(card900 is None,
                   f"лента «Все» содержит отменённый лот 900: {str(card900)[:150]!r}")
        card901 = ctx.page.evaluate("() => document.querySelector('#item-901')")
        ctx.expect(card901 is not None, "контрольный лот 901 (DRAFT) отсутствует в ленте «Все»")
        ctx.shot("e11_feed_no_cancelled")
    finally:
        for code, _, _ in FIXTURES:
            ctx.db("DELETE FROM item_photo WHERE item_code=%s", (code,))
            ctx.db("DELETE FROM lot WHERE item_id=%s OR code=%s", (code, code))
            ctx.db("DELETE FROM item WHERE code=%s", (code,))


def p_e13(ctx) -> None:
    """Хэши и строки аудита — внутри свёрнутого служебного блока."""
    ctx.nav("/ui/items/747")
    ctx.no_errors()
    det = ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"internal-block-audit\"]');"
        " return e ? {tag: e.tagName, open: e.open} : null; }"
    )
    ctx.expect(det is not None and det["tag"] == "DETAILS",
               "карточка: лента аудита не внутри свёрнутого блока <details>")
    ctx.expect(det is not None and det["open"] is False,
               "карточка: служебный блок аудита открыт (должен быть свёрнут по умолчанию)")
    body = ctx.body_text()
    ctx.expect("CURRENCY_REMIGRATION_V1" not in body,
               "карточка: строки аудита видны без раскрытия служебного блока")
    ctx.nav("/ui/draft/747")
    ctx.no_errors()
    det2 = ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"internal-block-hash\"]');"
        " return e ? {tag: e.tagName, open: e.open} : null; }"
    )
    ctx.expect(det2 is not None and det2["tag"] == "DETAILS",
               "черновик: хэш пэйлоада не внутри свёрнутого блока <details>")
    ctx.expect(det2 is not None and det2["open"] is False,
               "черновик: служебный блок хэша открыт (должен быть свёрнут по умолчанию)")
    # textContent — проверка, что хэш НАЛИЧЕН в блоке (работает и при
    # свёрнутом <details>: Chromium отдаёт '' для innerText скрытого
    # контента). Невидимость пользователю проверяется отдельно по
    # body.innerText ниже.
    hashline = ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"hash-info\"]');"
        " return e ? e.textContent : ''; }"
    )
    ctx.expect("HASH (SHA-256):" in hashline,
               f"строка хэша отсутствует или нечитаема: {hashline[:80]!r}")
    body = ctx.body_text()
    ctx.expect("HASH (SHA-256):" not in body,
               "черновик: хэш виден без раскрытия служебного блока")
    ctx.shot("e13_internal_collapsed")


def p_e14(ctx) -> None:
    """Заголовок страницы без опечатки."""
    ctx.nav("/ui/items")
    ctx.no_errors()
    title = ctx.page.evaluate("() => document.title")
    ctx.expect("рекомендации" in title,
               f"заголовок страницы: {title!r} (ожидалось «Консоль рекомендаций»)")
    ctx.expect("рекоммерции" not in title, f"опечатка в заголовке: {title!r}")


def p_e15(ctx) -> None:
    """Статусы лотов (лента/карточка/правка/сделка) выводятся по-русски."""
    expected = {
        "747": ("APPROVED", "Утверждён"),
        "106": ("DRAFT", "Черновик"),
        "348": ("PUBLISHED", "Опубликован"),
        "417": ("ACTIVE", "Активен"),
    }
    for code, (db_status, _label) in expected.items():
        row = ctx.db("SELECT status FROM item WHERE code=%s", (code,))
        actual = row[0][0] if row else None
        ctx.expect(bool(row) and row[0][0] == db_status,
                   f"фикстура сломана: статус {code} в БД = {actual!r} (ожидалось {db_status})")
    # Лента: бейдж статуса карточки == русское название статуса из БД.
    ctx.nav("/ui/items")
    ctx.no_errors()
    for code, (_db_status, label) in expected.items():
        badge = ctx.page.evaluate(
            "(c) => { const card = document.querySelector('#item-' + c);"
            " const b = card ? card.querySelectorAll('.badge') : [];"
            " return b.length > 1 ? b[b.length - 1].innerText.trim() : null; }",
            code,
        )
        ctx.expect(badge == label, f"лента {code}: статус {badge!r} (ожидалось «{label}»)")
    # Карточка: русское название есть, сырой английский токен не виден.
    ctx.nav("/ui/items/747")
    ctx.no_errors()
    body = ctx.body_text()
    ctx.expect("Утверждён" in body, "карточка 747: отсутствует русский статус «Утверждён»")
    ctx.expect("APPROVED" not in body, "карточка 747: виден сырой английский статус (APPROVED)")
    # Форма правки: статус лота по-русски.
    ctx.nav("/ui/lots/747/edit")
    ctx.no_errors()
    badge_text = ctx.page.evaluate(
        "() => { const b = document.querySelector('.badge-warning'); return b ? b.innerText.trim() : null; }"
    )
    lot_status = ctx.db("SELECT status FROM lot WHERE item_id='747'")[0][0]
    ctx.expect(badge_text is not None and STATUS_RU.get(lot_status, "…") in badge_text,
               f"форма правки: бейдж статуса лота {badge_text!r} (статус лота {lot_status!r})")
    # Сделки: статус сделки по-русски.
    rows = ctx.db("SELECT id FROM deal WHERE status='ACTIVE' ORDER BY created_at LIMIT 1")
    ctx.expect(bool(rows), "в копии БД нет активной сделки — фикстура сломана")
    ctx.nav(f"/ui/deals/{rows[0][0]}")
    ctx.no_errors()
    dbadge = ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"deal-status-badge\"]');"
        " return e ? e.innerText.trim() : null; }"
    )
    ctx.expect(dbadge == DEAL_STATUS_RU["ACTIVE"],
               f"сделка: статус {dbadge!r} (ожидалось «{DEAL_STATUS_RU['ACTIVE']}»)")
    rows2 = ctx.db("SELECT id FROM deal WHERE status='COMPLETED' LIMIT 1")
    ctx.expect(bool(rows2), "в копии БД нет завершённой сделки — фикстура сломана")
    ctx.nav(f"/ui/deals/{rows2[0][0]}")
    ctx.no_errors()
    dbadge2 = ctx.page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"deal-status-badge\"]');"
        " return e ? e.innerText.trim() : null; }"
    )
    ctx.expect(dbadge2 == DEAL_STATUS_RU["COMPLETED"],
               f"сделка: статус {dbadge2!r} (ожидалось «{DEAL_STATUS_RU['COMPLETED']}»)")
    ctx.shot("e15_russian_statuses")


def p_e19(ctx) -> None:
    """Справочник категорий: не менее 5 позиций, все в мастере, заголовки по-русски."""
    cdict = _category_dict()
    ctx.expect(len(cdict) >= 5,
               f"справочник категорий: позиций {len(cdict)} (требуется не менее 5): {sorted(cdict)}")
    ctx.nav("/ui/wizard")
    ctx.no_errors()
    opts = ctx.page.evaluate(
        "() => Array.from(document.querySelectorAll('#wz-category option')).map(o => o.value)"
    )
    ctx.expect(len(opts) >= len(cdict),
               f"мастер: категорий {len(opts)}, справочник объявляет {len(cdict)}: {opts}")
    missing = set(cdict) - set(opts)
    ctx.expect(not missing, f"категории справочника отсутствуют в мастере: {sorted(missing)}")
    for key, title in cdict.items():
        ctx.expect(re.search(r"[а-яА-Я]", title),
                   f"категория {key}: заголовок не на русском: {title!r}")
    for (db_key,) in ctx.db("SELECT DISTINCT category_key FROM item"):
        ctx.expect(db_key in cdict, f"используемая категория {db_key!r} не объявлена в справочнике")
    ctx.shot("e19_wizard_categories")


CHECKS = [
    ("P-E07", "блок «Дефекты» — только дефекты; особенности — отдельным блоком", p_e07),
    ("P-E09", "экран сделки без «N/A»; деградированный, но осмысленный текст", p_e09),
    ("P-E10", "единый словарь политики доставки: мастер/правка/конструктор + CHECK в БД", p_e10),
    ("P-E11", "лента «Все» без отменённых и удалённых лотов", p_e11),
    ("P-E13", "хэши и строки аудита внутри свёрнутого служебного блока", p_e13),
    ("P-E14", "заголовок страницы без опечатки", p_e14),
    ("P-E15", "статусы лотов выводятся по-русски", p_e15),
    ("P-E19", "справочник категорий: не менее 5 позиций, все в мастере, по-русски", p_e19),
]
