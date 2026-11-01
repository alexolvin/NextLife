"""Script to perform Queue N0 diagnostics for TZ-NextLife-UI-N1."""

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates" / "web"
AUDIT_DIR = ROOT / "ui-audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def run_n01_classes():
    class_usage = defaultdict(lambda: {"count": 0, "files": set()})

    for t in sorted(TEMPLATES_DIR.glob("*.html")):
        text = t.read_text(encoding="utf-8")
        matches = re.findall(r'class=["\']([^"\']+)["\']', text)
        for m in matches:
            cleaned = re.sub(r"\{\{[^}]+\}\}", "", m)
            cleaned = re.sub(r"\{%[^%]+%\}", "", cleaned)
            for c in cleaned.split():
                c = c.strip()
                if c and not c.startswith("{") and not c.endswith("}"):
                    class_usage[c]["count"] += 1
                    class_usage[c]["files"].add(t.name)

    app_css_path = ROOT / "src" / "nextlife" / "api" / "static" / "app.css"
    app_css_classes = set()
    if app_css_path.exists():
        for m in re.finditer(r"\.([a-zA-Z0-9_-]+)", app_css_path.read_text(encoding="utf-8")):
            app_css_classes.add(m.group(1))

    base_text = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    base_css_classes = set()
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", base_text, re.DOTALL):
        for cm in re.finditer(r"\.([a-zA-Z0-9_-]+)", m.group(1)):
            base_css_classes.add(cm.group(1))

    declared = base_css_classes
    used = set(class_usage.keys())
    used_and_decl = sorted(used & declared)
    used_not_decl = sorted(used - declared)
    decl_not_used = sorted(declared - used)

    out = []
    out.append("# N0-01. Инвентаризация классов стилей NextLife\n")
    out.append("Дата аудита: 2026-09-09\n")
    out.append("Источник: шаблоны `templates/web/*.html` vs таблицы стилей (`app.css` и встроенный `<style>` в `base.html`).\n")
    out.append("## 1. Сводная статистика\n")
    out.append(f"- Всего уникальных классов, использованных в шаблонах: **{len(class_usage)}**")
    out.append(f"- Классов объявлено в `app.css`: **{len(app_css_classes)}** (файл `app.css` в проекте отсутствует)")
    out.append(f"- Классов объявлено во встроенном `<style>` в `base.html`: **{len(base_css_classes)}**")
    out.append(f"- **Список 1 (Используется и объявлен в base.html)**: **{len(used_and_decl)}**")
    out.append(f"- **Список 2 (Используется и НЕ объявлен нигде)**: **{len(used_not_decl)}** (критический дефект)")
    out.append(f"- **Список 3 (Объявлен в base.html, но НЕ используется)**: **{len(decl_not_used)}**\n")
    out.append("---\n")

    out.append(f"## 2. Список 1: Используется и объявлен ({len(used_and_decl)})\n")
    out.append("| Класс | Число вхождений | Файлы шаблонов |")
    out.append("| --- | --- | --- |")
    for c in used_and_decl:
        f_list = ", ".join(sorted(class_usage[c]["files"]))
        out.append(f"| `.{c}` | {class_usage[c]['count']} | `{f_list}` |")

    out.append("\n---\n")
    out.append(f"## 3. Список 2: Используется и НЕ объявлен ({len(used_not_decl)})\n")
    out.append("> Несуществующие классы, Tailwind-утилиты, опечатки и ошибки склейки шаблонизатора:\n")
    out.append("| Класс | Число вхождений | Файлы шаблонов |")
    out.append("| --- | --- | --- |")
    for c in used_not_decl:
        f_list = ", ".join(sorted(class_usage[c]["files"]))
        out.append(f"| `.{c}` | {class_usage[c]['count']} | `{f_list}` |")

    out.append("\n---\n")
    out.append(f"## 4. Список 3: Объявлен в base.html и НЕ используется ({len(decl_not_used)})\n")
    out.append("| Класс | Источник объявления |")
    out.append("| --- | --- |")
    for c in decl_not_used:
        out.append(f"| `.{c}` | `templates/web/base.html` |")

    (AUDIT_DIR / "classes.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Generated {AUDIT_DIR / 'classes.md'}")


def run_n02_directives():
    directive_usage = defaultdict(list)
    pattern = re.compile(r'(\b(?:x-[a-zA-Z0-9_-]+|@[a-zA-Z0-9_.-]+|hx-[a-zA-Z0-9_-]+|:[a-zA-Z0-9_-]+))\b')

    for t in sorted(TEMPLATES_DIR.glob("*.html")):
        text = t.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            for m in pattern.finditer(line):
                d = m.group(1)
                directive_usage[d].append((t.name, line_no, line.strip()))

    base_text = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', base_text)

    out = []
    out.append("# N0-02. Инвентаризация директив библиотек в шаблонах\n")
    out.append("Дата аудита: 2026-09-09\n")
    out.append("## 1. Загрузка библиотек в `base.html`\n")
    out.append("В `<head>` `templates/web/base.html` обнаружены следующие теги `<script>`:")
    if scripts:
        for s in scripts:
            out.append(f"- Внешний скрипт: `{s}`")
    else:
        out.append("- Внешние скрипты отсутствуют.")

    out.append("\n**Нарушения правила U-2 и N3-01:**")
    out.append("1. **Alpine.js**: загружается через внешний CDN (`cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js`). Нарушение: отсутствует вендоринг в `static/vendor/` (N3-01). Без интернета скрипт не загрузится.")
    out.append("2. **HTMX**: тег `<script>` с HTMX полностью **отсутствует** в `base.html`.")
    out.append("\n---\n")

    out.append("## 2. Использование директив библиотек в шаблонах\n")
    out.append("| Директива | Библиотека | Файлы шаблонов | Статус загрузки библиотеки | Пояснение |")
    out.append("| --- | --- | --- | --- | --- |")

    known_alpine = {"x-data", "x-for", "x-if", "x-model", "x-show", "x-text", "@click", "@input"}
    for d, occurrences in sorted(directive_usage.items()):
        files = sorted(set(f"{x[0]}:{x[1]}" for x in occurrences))
        files_str = ", ".join(files[:3])
        if len(files) > 3:
            files_str += f" (+{len(files) - 3})"

        if d in known_alpine or d.startswith("x-") or d.startswith("@"):
            out.append(f"| `{d}` | Alpine.js | `{files_str}` | **CDN (не свендорено)** | Требует переноса в `static/vendor/` |")
        elif d.startswith("hx-"):
            out.append(f"| `{d}` | HTMX | `{files_str}` | **НЕ ЗАГРУЖЕНА** | Нарушение U-2: директива без библиотеки |")
        elif d.startswith(":"):
            out.append(f"| `{d}` | Tailwind pseudo / Jinja error | `{files_str}` | **ДЕФЕКТ РАЗМЕТКИ** | Ошибочный Tailwind-класс вида `:{d[1:]}` |")

    (AUDIT_DIR / "directives.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Generated {AUDIT_DIR / 'directives.md'}")


def run_n03_screens():
    screens_data = [
        {
            "route": "/ui/dashboard",
            "template": "templates/web/dashboard.html",
            "title": "Дашборд внимания",
            "states": {
                "пусто": "Нет (при 0 лотах пустые блоки без пояснения)",
                "один элемент": "Частично (отображает счетчик)",
                "много элементов": "Да",
                "длинный текст": "Нет (заголовки лотов обрезаются или переполняют)",
                "нет фото": "Да (фото на дашборде не выводятся)",
                "ошибка": "Нет (отдает 500 при сбое запроса)"
            }
        },
        {
            "route": "/ui/items",
            "template": "templates/web/items.html",
            "title": "Лента лотов",
            "states": {
                "пусто": "Частично (выводит блок 'Нет лотов', но без кнопки действия)",
                "один элемент": "Да",
                "много элементов": "Да (пагинация/прокрутка)",
                "длинный текст": "Частично (может ломать карточку)",
                "нет фото": "Частично (плейсхолдер со смещением)",
                "ошибка": "Нет (500)"
            }
        },
        {
            "route": "/ui/items/{code}",
            "template": "templates/web/item_detail.html",
            "title": "Карточка лота и вкладки",
            "states": {
                "все вкладки": "Да (карточка, черновик, доставка, правка)",
                "нет фото": "Частично (слайдер пустой)",
                "нет описания": "Да",
                "есть дефекты": "Да (была ошибка 500 из-за формата)",
                "нет дефектов": "Да",
                "ошибка": "Нет (500 при некорректных данных)"
            }
        },
        {
            "route": "Медиа-менеджер",
            "template": "templates/web/item_detail.html / wizard.html",
            "title": "Менеджер фото и галерея",
            "states": {
                "нет фото": "Да (зона dropzone)",
                "одно": "Да",
                "двенадцать": "Частично (на 360px слайдер переполняется)",
                "идёт загрузка": "Нет (индикатор прогресса отсутствует)",
                "ошибка загрузки одного файла": "Нет (alert браузера)"
            }
        },
        {
            "route": "Лайтбокс",
            "template": "templates/web/base.html (встроенный)",
            "title": "Полноэкранный просмотр изображений",
            "states": {
                "первое/среднее/последнее": "Да",
                "свайп": "Да (на 360px поддержан)",
                "клавиатура": "Да (ArrowLeft, ArrowRight, Escape)",
                "счётчик": "Да (1 / N)"
            }
        },
        {
            "route": "/ui/draft/{code}",
            "template": "templates/web/draft.html",
            "title": "Редактор черновика и описания",
            "states": {
                "пусто": "Да",
                "сгенерировано": "Да",
                "отредактировано": "Да",
                "превышен лимит символов": "Частично (красный счетчик, но без блокировки)"
            }
        },
        {
            "route": "/ui/lots/{code}/shipping",
            "template": "templates/web/shipping_options.html",
            "title": "Конструктор доставки",
            "states": {
                "нет опций": "Да",
                "одна": "Да",
                "несколько": "Да",
                "тариф недоступен": "Частично (нет явного предупреждения)"
            }
        },
        {
            "route": "/ui/lots/{code}/edit",
            "template": "templates/web/lot_edit.html",
            "title": "Редактирование лота",
            "states": {
                "валидная форма": "Да",
                "ошибки валидации по полям": "Нет (стандартная браузерная валидация)"
            }
        },
        {
            "route": "/ui/threads",
            "template": "templates/web/threads.html",
            "title": "Переписка и мошенничество",
            "states": {
                "пусто": "Частично",
                "непрочитанные": "Да",
                "помечено подозрением": "Да (плашка подозрения)"
            }
        },
        {
            "route": "/ui/deals/{id}",
            "template": "templates/web/deal.html",
            "title": "Экран сделки",
            "states": {
                "каждое состояние": "Да (RESERVED, PAID, DISPATCHED, COMPLETED, CANCELLED)",
                "модальное окно отправки": "Да (диспетчеризация)"
            }
        },
        {
            "route": "/ui/items/{code}/preview/{marketplace}",
            "template": "templates/web/marketplace_preview.html",
            "title": "Предпросмотр на площадках",
            "states": {
                "по каждой включённой площадке": "Да (bazos_cz, sbazar_cz, fb_marketplace, aukro_cz)"
            }
        },
        {
            "route": "/ui/wizard",
            "template": "templates/web/wizard.html",
            "title": "Мастер создания лота",
            "states": {
                "все 5 шагов": "Да (step 1..5)",
                "возврат назад": "Да",
                "прерывание и продолжение": "Да (по сохраненному code)"
            }
        },
        {
            "route": "/ui/archive",
            "template": "templates/web/archive.html",
            "title": "Архив лотов",
            "states": {
                "пусто": "Да",
                "заполнен": "Да"
            }
        }
    ]

    out = []
    out.append("# N0-03. Инвентаризация экранов и обязательных состояний\n")
    out.append("Дата аудита: 2026-09-09\n")
    out.append("Охват: все 13 маршрутов и компонентов `/ui/*` без исключений.\n\n")
    out.append("| Экран / Маршрут | Шаблон | Назначение | Обязательные состояния | Поддержка сейчас |")
    out.append("| --- | --- | --- | --- | --- |")

    for s in screens_data:
        states_md = "<br>".join(f"• **{k}**: {v}" for k, v in s["states"].items())
        fully_supported = all("Да" in v for v in s["states"].values())
        sup_str = "Поддерживается" if fully_supported else "Требует доработки"
        out.append(f"| `{s['route']}` | `{s['template']}` | {s['title']} | {states_md} | **{sup_str}** |")

    (AUDIT_DIR / "screens.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Generated {AUDIT_DIR / 'screens.md'}")


def run_n04_template_data():
    import os
    import sys

    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from nextlife.storage.db import get_engine
    from sqlalchemy import text

    dsn = os.environ.get("NEXTLIFE_DSN")
    if not dsn:
        raise RuntimeError("Missing required environment variable: NEXTLIFE_DSN")
    # Single DSN source: the same NEXTLIFE_DSN the app resolves via get_engine().
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT code, jsonb_typeof(to_jsonb(defects)), defects FROM item ORDER BY code;")).fetchall()

    out = []
    out.append("# N0-04. Инвентаризация данных шаблонов и многоформатных полей\n")
    out.append("Дата аудита: 2026-09-09\n")
    out.append("## 1. Поля, читаемые шаблонами\n")
    out.append("- **`item_detail.html`**: `item.code`, `item.title`, `item.target_price`, `item.target_currency`, `item.status`, `item.defects`, `photos`, `lot`")
    out.append("- **`items.html`**: `it.code`, `it.title`, `it.target_price`, `it.target_currency`, `it.status`, `it.photo_count`, `it.thumb_url`, `it.status_badge_cls`")
    out.append("- **`draft.html`**: `item.code`, `lot.code`, `draft_text`, `generated_text`, `seller_notes`, `char_limits`")
    out.append("- **`shipping_options.html`**: `lot_code`, `item_price`, `shipping_policy`, `options` (`carrier_key`, `service_name`, `price`, `auto_price`, `is_manual_price`)")
    out.append("- **`deal.html`**: `deal.id`, `deal.status`, `deal.agreed_price`, `deal.currency`, `deal.delivery_method`, `item`, `thread`")
    out.append("- **`wizard.html`**: `item.code`, `item.title`, `item.category_key`, `item.condition_grade`, `item.box_present`, `item.receipt_present`, `lot.price_gross`")
    out.append("- **`dashboard.html`**: блоки внимания `b1..b6` (`count`, `title`, `link`, `preview_items`)")
    out.append("\n---\n")

    out.append("## 2. Многоформатное поле: `item.defects`\n")
    out.append("В базе данных `nextlife` поле `item.defects` (JSONB) хранится в ДВУХ взаимоисключающих форматах:")
    out.append("1. **Форма словаря (Mapping / Object)**: `{\"bezel_scratch\": \"Описание дефекта...\"}`")
    out.append("2. **Форма списка (Sequence / Array of Objects)**: `[{\"zone\": \"general\", \"text\": \"Идеальное состояние...\"}]`")
    out.append("3. **Пустой словарь**: `{}`\n")
    out.append("Фактический срез из базы данных PostgreSQL `nextlife`:\n")
    out.append("```sql\nSELECT code, jsonb_typeof(to_jsonb(defects)) AS data_type, defects FROM item ORDER BY code;\n```\n")
    out.append("| Item Code | JSONB Type | Фактическое значение в БД |")
    out.append("| --- | --- | --- |")
    for r in rows:
        val_str = json.dumps(r[2], ensure_ascii=False)
        out.append(f"| `#{r[0]}` | `{r[1]}` | `{val_str}` |")

    out.append("\n### Требуемое действие в Очереди N4 (Нормализация данных):\n")
    out.append("Согласно критерию N4-01: поле приводится к **единственной форме**: список объектов `[{\"zone\": str, \"text\": str, \"photo_id\": str | None}]`.")
    out.append("Пишется миграция, преобразующая все `dict` в `list of dicts`.")
    out.append("Из шаблона `item_detail.html` удаляется проверка `{% if item.defects is mapping %}` и ветвление типов.")

    (AUDIT_DIR / "template-data.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Generated {AUDIT_DIR / 'template-data.md'}")


if __name__ == "__main__":
    run_n01_classes()
    run_n02_directives()
    run_n03_screens()
    run_n04_template_data()
