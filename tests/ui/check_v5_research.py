"""TZ-NextLife-UI-N2 V5 (Разведка) — проверки P-R07, P-R08.

P-R07   сервис разведки провязан с API и интерфейсом: кнопка запуска на
       шаге 4 мастера, в карточке и в черновике; запуск создаёт run в БД;
       отчёт показывает диапазон цен только с числом аналогов и ссылками
       (выборка не меньше порога), иначе — явное сообщение о недостаточной
       выборке. Диапазон без источников не выводится (П-1.3, Э-1).
P-R08   на основе найденных аналогов — 2-3 варианта чешского текста,
       редактируемые; выбранный сохраняется как исходящий текст (final_text).
       Без найденных аналогов вариантов нет (диапазон/текст без источников
       не выводится).

Окружение (Э-10): копия nextlife_check, чек-сервер 127.0.0.1:8083.
Определённость:
- реальный клик кнопки запускает реальные коннекторы; в копии fx_rate пуст,
  поэтому каждый raw-аналог пропускается (UnknownCurrencyError) и run
  детерминированно завершается с 0 собранных результатов — честное
  сообщение о недостаточной выборке;
- диапазон/ссылки/недостаточность проверяются фикстурами research_run +
  research_result (CZK), удаляемыми в finally.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

# Спецификация (ТЗ §4 V5, config/nextlife.yaml: min_comparables=5).
MIN_COMPARABLES = 5

# Предметы копии: 747 — APPROVED, CZK, без лота; 106 — DRAFT, CZK.
CARD_ITEM = "747"
TEXT_ITEM = "106"

PANEL_SEL = '[data-testid="research-panel"]'
BTN_SEL = '[data-testid="btn-research-run"]'
# Хвостовой «-» отличает textarea вариантов от контейнера research-variants.
VARIANT_SEL = '[data-testid^="research-variant-"]'

# Чешские диакритики (латиница, не кириллица) — признак чешского текста.
CZ_CHARS = set("áčďéěíňóřšťúůýž")


def _cyrillic_dominant(text: str) -> bool:
    """Эвристика как в P-E03: >=50% букв — кириллица (U+0400..U+04FF)."""
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 3:
        return False
    cyr = sum(1 for ch in letters if "\u0400" <= ch <= "\u04FF")
    return cyr / len(letters) >= 0.5


def _panel_text(ctx) -> str:
    return ctx.page.evaluate(
        f"() => {{ const e = document.querySelector('{PANEL_SEL}');"
        " return e ? e.textContent : ''; }"
    )


def _insert_fixture_run(
    ctx, item_code: str, prices: list[int], start: datetime
) -> str:
    """Фикстура: FINISHED run + N результатов в CZK с реальными ссылками."""
    run_id = str(uuid.uuid4())
    ctx.db(
        "INSERT INTO research_run (id, item_code, started_at, finished_at, status, "
        "marketplaces, error, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 'FINISHED', %s::jsonb, '{}'::jsonb, NOW(), NOW())",
        (run_id, item_code, start, start + timedelta(minutes=1),
         json.dumps({"bazos_cz": True, "sbazar_cz": True})),
    )
    for i, price in enumerate(prices):
        mp = "bazos_cz" if i % 2 == 0 else "sbazar_cz"
        domain = "www.bazos.cz" if mp == "bazos_cz" else "www.sbazar.cz"
        ctx.db(
            "INSERT INTO research_result (id, run_id, marketplace_key, external_url, "
            "title, price, currency, price_base, fx_rate_date, condition_text, "
            "raw, captured_at, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'CZK', %s, CURRENT_DATE, 'bez vad', "
            "'{}'::jsonb, NOW(), NOW(), NOW())",
            (str(uuid.uuid4()), run_id, mp,
             f"https://{domain}/inzerat/v5-{item_code}-{i}.html",
             f"Sony WH-1000XM4, stav jako novy (fixture {i})",
             str(price), str(price)),
        )
    return run_id


def _delete_run(ctx, run_id: str) -> None:
    ctx.db("DELETE FROM research_result WHERE run_id=%s", (run_id,))
    ctx.db("DELETE FROM research_run WHERE id=%s", (run_id,))


def _allow_long_request(ctx) -> None:
    """Реальный запуск разведки ходит в живые площадки (таймаут коннектора 20 c)."""
    ctx.page.set_default_timeout(180_000)
    ctx.page.set_default_navigation_timeout(180_000)


def _assert_panel_present(ctx, what: str) -> str:
    panel = _panel_text(ctx)
    ctx.expect(bool(panel.strip()), f"{what}: блок разведки отсутствует или пуст")
    ctx.expect("разведк" in panel.lower(),
               f"{what}: блок разведки не назван: {panel[:120]!r}")
    btn = ctx.page.query_selector(BTN_SEL)
    ctx.expect(btn is not None, f"{what}: кнопка запуска разведки отсутствует")
    return panel


def p_r07(ctx) -> None:
    """Провязка: кнопки в трёх местах; run в БД; диапазон только с источниками."""
    created: list[str] = []
    try:
        # 1. Блок + кнопка на всех трёх экранах; без run — ни диапазона,
        #    ни вариантов (честное пустое состояние).
        urls = {
            f"мастер шаг 4 /ui/wizard/{CARD_ITEM}?step=4": f"/ui/wizard/{CARD_ITEM}?step=4",
            f"карточка /ui/items/{CARD_ITEM}": f"/ui/items/{CARD_ITEM}",
            f"черновик /ui/draft/{CARD_ITEM}": f"/ui/draft/{CARD_ITEM}",
        }
        for what, url in urls.items():
            ctx.nav(url)
            ctx.no_errors()
            _assert_panel_present(ctx, what)
            variants = ctx.page.query_selector_all(VARIANT_SEL)
            ctx.expect(len(variants) == 0,
                       f"{what}: без найденных аналогов выведены варианты текста")
            ctx.expect("Похожие лоты:" not in _panel_text(ctx),
                       f"{what}: без выборки выведен диапазон цен")
        ctx.shot("r07_panel_three_screens")

        # 2. Реальный клик по кнопке (карточка): run создаётся в БД,
        #    экран показывает честный отчёт. В копии fx_rate пуст ->
        #    0 собранных аналогов -> явное сообщение, без диапазона.
        ctx.nav(f"/ui/items/{CARD_ITEM}")
        _allow_long_request(ctx)
        ctx.page.click(BTN_SEL)
        ctx.no_errors()
        rows = ctx.db(
            "SELECT id, status FROM research_run WHERE item_code=%s "
            "ORDER BY started_at DESC LIMIT 1",
            (CARD_ITEM,),
        )
        ctx.expect(bool(rows), "после запуска разведки run в БД не создан")
        ctx.expect(rows[0][1] == "FINISHED",
                   f"run не завершён: статус {rows[0][1]!r}")
        created.append(rows[0][0])
        panel = _panel_text(ctx)
        ctx.expect("Найдено аналогов: 0" in panel,
                   f"честный отчёт пустого run (в копии нет курсов валют): {panel[:200]!r}")
        ctx.expect("Похожие лоты:" not in panel,
                   "без аналогов выведен диапазон цен")
        ctx.expect("недостаточна" in panel.lower(),
                   f"нет явного сообщения о недостаточной выборке: {panel[:200]!r}")
        ctx.shot("r07_real_run")

        # 3. Выборка достаточна (6 >= 5): диапазон + число + ссылки,
        #    валюта предмета (CZK), не базовая.
        start_a = datetime.now(UTC) + timedelta(hours=2)
        prices_a = [8000, 9000, 10000, 10500, 11000, 12000]
        run_a = _insert_fixture_run(ctx, CARD_ITEM, prices_a, start_a)
        created.append(run_a)
        ctx.nav(f"/ui/items/{CARD_ITEM}")
        ctx.no_errors()
        panel = _panel_text(ctx)
        ctx.expect(f"Найдено аналогов: {len(prices_a)}" in panel,
                   f"число найденных аналогов неверно: {panel[:200]!r}")
        ctx.expect("Похожие лоты:" in panel,
                   f"при достаточной выборке диапазон не выведен: {panel[:200]!r}")
        ctx.expect("8 000 Kč" in panel, f"мин. цена диапазона не в Kč: {panel[:200]!r}")
        ctx.expect("12 000 Kč" in panel, f"макс. цена диапазона не в Kč: {panel[:200]!r}")
        ctx.expect("eur" not in panel.lower(),
                   "базовая (отчётная) валюта выведена на экране")
        links = ctx.page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "'[data-testid=\"research-links\"] a')).map(a => a.href)"
        )
        ctx.expect(len(links) == 5,
                   f"ожидаются 5 ссылок на аналоги, найдено {len(links)}")
        for i in range(5):
            ctx.expect(f"https://www.bazos.cz/inzerat/v5-{CARD_ITEM}-{i}.html" in links
                       or f"https://www.sbazar.cz/inzerat/v5-{CARD_ITEM}-{i}.html" in links,
                       f"ссылка на аналог {i} отсутствует в отчёте")
        ctx.expect(f"https://www.bazos.cz/inzerat/v5-{CARD_ITEM}-5.html" not in links
                   and f"https://www.sbazar.cz/inzerat/v5-{CARD_ITEM}-5.html" not in links,
                   "выведено больше 5 ссылок")
        ctx.shot("r07_range_with_sources")

        # 4. Выборка меньше порога (2 < 5): диапазона нет,
        #    явное сообщение с числом и порогом.
        start_b = datetime.now(UTC) + timedelta(hours=3)
        run_b = _insert_fixture_run(ctx, CARD_ITEM, [9500, 10200], start_b)
        created.append(run_b)
        ctx.nav(f"/ui/items/{CARD_ITEM}")
        ctx.no_errors()
        panel = _panel_text(ctx)
        ctx.expect("Найдено аналогов: 2" in panel,
                   f"число найденных аналогов неверно: {panel[:200]!r}")
        ctx.expect("Похожие лоты:" not in panel,
                   "при выборке меньше порога выведен диапазон цен")
        ctx.text_has(
            panel,
            f"Выборка недостаточна для рыночной оценки: найдено 2 (требуется {MIN_COMPARABLES}).",
            "явное сообщение о недостаточной выборке с числом и порогом",
        )
        ctx.shot("r07_insufficient_sample")
    finally:
        for run_id in created:
            _delete_run(ctx, run_id)


def p_r08(ctx) -> None:
    """2-3 редактируемых чешских варианта по аналогам; сохранение в final_text."""
    created: list[str] = []
    orig_state: tuple[str, str | None] | None = None
    try:
        rows = ctx.db(
            "SELECT attrs::text, notes FROM item WHERE code=%s", (TEXT_ITEM,)
        )
        ctx.expect(bool(rows), f"предмет {TEXT_ITEM} отсутствует в копии")
        orig_state = (rows[0][0], rows[0][1])
        title_row = ctx.db("SELECT title FROM item WHERE code=%s", (TEXT_ITEM,))
        title = title_row[0][0]

        run_id = _insert_fixture_run(
            ctx, TEXT_ITEM, [8000, 9000, 10000, 10500, 11000, 12000],
            datetime.now(UTC) + timedelta(hours=2),
        )
        created.append(run_id)

        ctx.nav(f"/ui/draft/{TEXT_ITEM}")
        ctx.no_errors()
        values = ctx.page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "'[data-testid^=\"research-variant-\"]')).map(e => e.value)"
        )
        ctx.expect(2 <= len(values) <= 3,
                   f"ожидаются 2-3 варианта текста, найдено {len(values)}")
        for i, val in enumerate(values, 1):
            ctx.expect(len(val) >= 40,
                       f"вариант {i} подозрительно короткий: {val[:80]!r}")
            ctx.expect(any(c in val for c in CZ_CHARS),
                       f"вариант {i} не содержит чешских диакритик: {val[:80]!r}")
            ctx.expect(not _cyrillic_dominant(val),
                       f"вариант {i} преимущественно кириллица: {val[:80]!r}")
            ctx.expect(title in val,
                       f"вариант {i} не содержит название предмета: {val[:80]!r}")
        disabled = ctx.page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "'[data-testid^=\"research-variant-\"]'))"
            ".some(e => e.disabled || e.readOnly)"
        )
        ctx.expect(not disabled, "вариант текста отредактирован не будет — поле неактивно")
        ctx.shot("r08_variants_draft")

        # Карточка: тот же блок вариантов.
        ctx.nav(f"/ui/items/{TEXT_ITEM}")
        ctx.no_errors()
        card_values = ctx.page.query_selector_all(VARIANT_SEL)
        ctx.expect(2 <= len(card_values) <= 3,
                   f"на карточке вариантов {len(card_values)}, ожидается 2-3")

        # Редактирование + сохранение как исходящий текст (final_text, P-E12).
        ctx.nav(f"/ui/draft/{TEXT_ITEM}")
        edited = f"{values[0]} Urezeno kontrolou V5 (dokoncene)."
        ctx.page.fill('[data-testid="research-variant-1"]', edited)
        _allow_long_request(ctx)
        ctx.page.click('[data-testid="research-apply-1"]')
        ctx.no_errors()
        saved = ctx.db(
            "SELECT attrs->>'final_text', notes, status FROM item WHERE code=%s",
            (TEXT_ITEM,),
        )
        ctx.expect(saved[0][0] == edited,
                   f"отредактированный вариант не сохранён в final_text: {str(saved[0][0])[:120]!r}")
        ctx.expect(saved[0][2] == "DRAFT",
                   f"после сохранения текста статус предмета не DRAFT: {saved[0][2]!r}")
        listing_val = ctx.page.evaluate(
            "() => { const e = document.getElementById('listing-text-input');"
            " return e ? e.value : ''; }"
        )
        ctx.expect(edited in listing_val,
                   "сохранённый текст не виден в поле черновика")
        ctx.shot("r08_saved_as_final_text")
    finally:
        for run_id in created:
            _delete_run(ctx, run_id)
        if orig_state is not None:
            ctx.db(
                "UPDATE item SET attrs=%s::jsonb, notes=%s, updated_at=NOW() "
                "WHERE code=%s",
                (orig_state[0], orig_state[1], TEXT_ITEM),
            )


CHECKS = [
    ("P-R07", "Разведка: провязка API/UI, диапазон только с источниками", p_r07),
    ("P-R08", "Разведка: 2-3 редактируемых чешских варианта, сохранение в final_text", p_r08),
]
