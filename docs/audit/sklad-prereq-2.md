# Аудит 2: доп. вопросы к превращению «Ленты» в «Склад»

Формат: **Факт — Источник**. Источник = `file:line`, таблица/колонка или вывод выполненной команды.
Статусы: РЕАЛИЗОВАНО / ЗАГЛУШКА / НЕ РЕАЛИЗОВАНО / НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ.
Код, БД, конфиги не менялись; коммитов нет.

---

## A. ITEM vs LOT — источник истины

### A1. Таблица по полям

| Поле | Колонка в `item` | Колонка в `lot` | Кто пишет | Кто читает |
|---|---|---|---|---|
| Название | `item.title` String(255) | — (нет) | `create_item_and_lot` `lot_creation.py:106`; wizard step1 `routes_lots.py:273`; wizard text `routes_lots.py:836` | `oc_item_show` `routes_openclaw.py:126`; `item_detail.html` |
| Цена | `item.target_price` Numeric(12,2) | `lot.price_gross` Numeric(12,2) | `create_item_and_lot` `lot_creation.py:105,124`; wizard step4 `routes_lots.py:300-303`; `wizard_finalize.py` | `publish.py:204` (`lot.price_gross or item.target_price`) |
| Нижняя цена | — (нет) | `lot.price_floor` Numeric(12,2) | `create_item_and_lot` `lot_creation.py:125`; wizard step4 `routes_lots.py:304` | `lot_edit.html` |
| Валюта | `item.target_currency` String(3) | `lot.currency` String(3) default CZK | `create_item_and_lot` `lot_creation.py:106,126` | `publish.py` |
| Статус | `item.status` String(50) | `lot.status` String(50) default DRAFT | `create_item_and_lot`; `lot_delete.py`; `unpublish.py`; `publish.py`; `deals.py` | все |
| Заметки | `item.notes` Text | `lot.notes` Text | `create_item_and_lot` `lot_creation.py:107,127` | `draft_text.py` |
| Описание | `item.attrs["final_text"/"generated_description"/"listing_text_cs"/"description"/"wizard_generated_text"/"wizard_frames"]` | `item.attrs` (то же) | `routes.py:451` (final_text); `lot_edit.py:145`; `wizard_textgen.py` (wizard_generated_text); `wizard_localize.py` (wizard_frames) | `draft_text.get_item_text_blocks` `draft_text.py:28-67` |
| Доставка / shipping_policy | — (нет) | `lot.shipping_policy` String(20) default BUYER_PAYS | `create_item_and_lot` `lot_creation.py:128`; wizard step5 `routes_lots.py:313` | `shipping_options.py` |

**Вывод:** `item` хранит название, цену, валюту, статус, заметки (физический предмет); `lot` хранит цену/нижнюю цену/валюту/статус/заметки/delivery_policy (коммерческое предложение). Описание — только в `item.attrs`. — Источник: `alembic/versions/20260907_0001_initial_schema.py:22-37` (item), `20260908_0008_add_lot_entity_and_separation.py:21-36` (lot).

### A2. Совпадают ли item.code и lot.code

**Да, по дизайну совпадают.** В `create_item_and_lot` обе записи получают один и тот же `code`:
- `Item(code=code, ...)` — `lot_creation.py:112`
- `Lot(code=code, item_id=item.code, ...)` — `lot_creation.py:123`

**Проверка по данным: НЕТ ДОСТУПА** (peer-auth требует OS-пользователя `nextlife`, у агента нет sudo). Команда для владельца:
```
cd /path/to/NextLife && sudo -u nextlife psql "postgresql:///nextlife?host=/var/run/postgresql" -c "SELECT count(*) FILTER (WHERE i.code<>l.code) AS mismatch, count(*) AS total FROM item i JOIN lot l ON l.item_id=i.code;"
```
— Источник: `src/nextlife/services/lot_creation.py:112,123`; `systemctl cat nextlife-web`.

### A3. Item без lot / lot без item

**В коде:**
- `create_item_and_lot` всегда создаёт Item и Lot вместе (`lot_creation.py:110-130`).
- `_prune_wizard_drafts` удаляет ItemPhoto + Lot + Item разом (`wizard_draft.py:49-51`).
- `delete_lot` — soft-delete лота (строка Lot остаётся со статусом DELETED), Item не удаляется (`lot_delete.py:101-107`).
- `lot.item_id` — FK→item.code ON DELETE CASCADE, лот всегда ссылается на item (`alembic/versions/20260908_0008_add_lot_entity_and_separation.py:31`).

**Вывод:** в нормальных путях каждый Item имеет строку Lot; Lot без Item невозможен (FK). Item без *активного* Lot возможен после `delete_lot` (Lot DELETED). — Источник: `lot_creation.py:110-130`, `wizard_draft.py:49-51`, `lot_delete.py:101-107`.

**В данных:** НЕТ ДОСТУПА (см. A2).

### A4. Код `create_item_and_lot()`

```python
def create_item_and_lot(
    session: Session,
    title: str,
    category_key: str,
    price: Decimal | None = None,
    currency: str | None = None,
    condition_key: str = "used",
    price_floor: Decimal | None = None,
    shipping_policy: str = "BUYER_PAYS",
    notes: str | None = None,
    attrs: dict[str, Any] | None = None,
    code_release_days: int = 90,
) -> dict[str, Any]:
    """Create physical Item and its commercial Lot offering in a single transaction."""
    title_clean = str(title).strip()
    category_clean = str(category_key).strip()

    if not title_clean:
        raise InputError("Missing required title argument.")
    if not category_clean:
        raise InputError("Missing required category argument.")

    if currency is None:
        currency = derive_lot_currency(session)

    # 1. Allocate 3-character unique code
    code = allocate_code(session, code_release_days)

    # 2. Physical Item
    item = Item(
        code=code,
        kind="SELL",
        title=title_clean,
        category_key=category_clean,
        attrs=attrs or {},
        condition_key=condition_key,
        defects=[],
        status=ItemStatus.DRAFT.value,
        target_price=price,
        target_currency=currency,
        notes=notes,
    )
    session.add(item)
    session.flush()

    # 3. Commercial Lot
    floor_val = price_floor if price_floor is not None else (price or Decimal("0"))
    lot = Lot(
        code=code,
        item_id=item.code,
        status="DRAFT",
        price_gross=price,
        price_floor=floor_val,
        currency=currency,
        shipping_policy=shipping_policy,
        notes=notes,
    )
    session.add(lot)
    session.flush()

    # 4. Questionnaire from category
    questionnaire = load_category_questionnaire(category_clean)

    return {
        "status": "created",
        "item_code": item.code,
        "lot_code": lot.code,
        "display_code": format_code_display(lot.code),
        "tag": format_code_tag(lot.code),
        "title": item.title,
        "category": item.category_key,
        "price": str(price) if price is not None else None,
        "currency": currency,
        "shipping_policy": lot.shipping_policy,
        "questionnaire": questionnaire,
    }
```
— Источник: `src/nextlife/services/lot_creation.py:70-140`.

### A5. Актуальность схемы

- `alembic heads` → **`0021 (head)`**. — Источник: вывод `.venv/bin/python -m alembic heads` (с `NEXTLIFE_DSN`-заглушкой).
- `alembic current` → **НЕТ ДОСТУПА** (peer-auth: `password authentication failed for user "dummy"`; требуется OS-пользователь `nextlife`). Команда для владельца:
```
cd /path/to/NextLife && sudo -u nextlife .venv/bin/python -m alembic current
```
— Источник: вывод `.venv/bin/python -m alembic current` (ошибка подключения).

## B. Снятие и удаление

### B1. `delete_lot` — что происходит

Код основной функции `delete_lot` (`lot_delete.py:58-135`):

```python
def delete_lot(
    session: Session,
    lot_code: str,
    *,
    force_unpublish: bool = True,
    actor: str = "OWNER",
) -> dict[str, Any]:
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ValueError(f"Lot with code {lot_code} not found")

    item = session.scalar(select(Item).where(Item.code == lot.item_id))
    if not item:
        raise ValueError(f"Item {lot.item_id} linked to lot {lot_code} not found")

    # 1. Active deal check
    can_delete, reason = can_delete_lot(session, lot_code)
    if not can_delete:
        raise LotDeletionBlockedError(reason)

    # 2. Mandatory unpublishing from marketplaces
    listings = session.scalars(
        select(Listing).where(Listing.item_code == item.code)
    ).all()

    unpublished_keys = []
    now = datetime.now(UTC)
    for listing in listings:
        if listing.status in ("PUBLISHED", "PARTIALLY_PUBLISHED", "ACTIVE"):
            listing.status = "ARCHIVED"
            listing.archived_at = now
            unpublished_keys.append(listing.marketplace_key)

    # 3. Soft delete the lot
    previous_lot_status = lot.status
    lot.status = "DELETED"

    # If item has no other non-deleted lots, update item status
    other_active_lots = session.scalars(
        select(Lot).where(Lot.item_id == item.code, Lot.code != lot_code, Lot.status != "DELETED")
    ).all()
    if not other_active_lots and item.status != ItemStatus.SOLD.value:
        item.status = ItemStatus.CANCELLED.value

    # 4. Audit trail
    record_audit_event(
        session=session, actor=actor, action="LOT_DELETED",
        entity="lot", entity_id=lot.code,
        data={"item_code": item.code, "previous_status": previous_lot_status,
              "new_status": "DELETED", "unpublished_marketplaces": unpublished_keys,
              "media_preserved": True},
    )
    return {...}
```
— Источник: `src/nextlife/services/lot_delete.py:58-135`.

**Что происходит:**
- **item**: статус → `CANCELLED`, если нет других non-DELETED лотов (и item не SOLD) — `lot_delete.py:104-107`.
- **listing**: все `PUBLISHED`/`PARTIALLY_PUBLISHED`/`ACTIVE` → `ARCHIVED` + `archived_at` — `lot_delete.py:88-92`.
- **фото в БД**: сохраняются (`media_preserved: True`, удаления строк ItemPhoto нет).
- **файлы фото на диске**: сохраняются (функция не трогает файлы).
- **объявления на площадках**: **не обращается к площадкам** — только локально `listing.status = "ARCHIVED"`. Вызова `connector.unpublish`/`archive` в `delete_lot` нет.
- **deal**: блокирует удаление, если есть активная сделка (`can_delete_lot`, `lot_delete.py:31-55`).
- **thread**: не обрабатывается.

### B2. `services/unpublish.py` — обращается ли к площадке

Основная функция `execute_unpublish_tasks` (`unpublish.py:131-270`). Обращение к площадке — **условное**:

```python
elif mp_key in connector_map:
    conn = connector_map[mp_key]
    try:
        if hasattr(conn, "archive"):
            conn.archive(listing.external_id if listing else "")
        elif hasattr(conn, "unpublish"):
            conn.unpublish(listing.external_id if listing else "")
    except Exception as exc:
        should_succeed = False
        error_msg = str(exc)
```
— Источник: `src/nextlife/services/unpublish.py:170-176`.

**Вывод:** по умолчанию (`connector_map = {}`) площадка **не вызывается** — задача помечается `COMPLETED`, `listing.status = "ARCHIVED"` (локально). Обращение к коннектору (`conn.archive`/`conn.unpublish`) происходит **только если передан `connector_map`**. Метод `archive` у коннекторов не определён (в `base.py` есть `unpublish`), поэтому фактически вызывается `conn.unpublish`. — Источник: `unpublish.py:139,170-176`; `base.py:271-273`.

### B3. Операции по площадкам, где publish не ЗАГЛУШКА

| Площадка | Создать | Изменить | Снять | Проверить статус | Обнаружить продажу |
|---|---|---|---|---|---|
| **bazos_cz** | РЕАЛИЗОВАНО (Playwright, `bazos_publish.py`) — НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ | НЕ РЕАЛИЗОВАНО | НЕ РЕАЛИЗОВАНО | НЕ РЕАЛИЗОВАНО | НЕ РЕАЛИЗОВАНО |
| **aukro_cz** | ЗАГЛУШКА (`CapabilityNotSupported`, `aukro.py:181`) | ЗАГЛУШКА (`aukro.py:188`) | ЗАГЛУШКА (`aukro.py:196`) | НЕ РЕАЛИЗОВАНО (есть `search`) | НЕ РЕАЛИЗОВАНО |
| **sbazar_cz** | ЗАГЛУШКА (`ConnectorError`, `sbazar.py:41`) | ЗАГЛУШКА (`sbazar.py:52`) | ЗАГЛУШКА (`sbazar.py:59`) | НЕ РЕАЛИЗОВАНО (есть `search`) | НЕ РЕАЛИЗОВАНО |

— Источник: `src/nextlife/services/bazos_publish.py`; `src/nextlife/connectors/aukro.py:169-199`; `src/nextlife/connectors/sbazar.py:33-60`.

### B4. Aukro FEED-канал

**Противоречие устранено.** В docstring `aukro.py:10-16` описан FEED-канал (XML `aukro-feed.xml`, который Manažer prodeje mp.aukro.cz забирает по URL), но **в коде он не реализован**: метод `publish()` бросает `CapabilityNotSupported` (`aukro.py:181`), кода отправки XML нет.

**Вывод:** FEED-канал — **НЕ РЕАЛИЗОВАНО** (только описание в docstring). `publish()` — ЗАГЛУШКА. Отправка XML **не выполнялась**. — Источник: `src/nextlife/connectors/aukro.py:10-16,169-185`.

### B5. Listings при переводе item в SOLD

`create_deal` переводит item в SOLD, но **не трогает listings**:

```python
# Transition item to SOLD
item = session.get(Item, listing.item_code)
if item and item.status != ItemStatus.SOLD.value:
    validate_transition(ItemStatus(item.status), ItemStatus.SOLD)
    item.status = ItemStatus.SOLD.value
```
— Источник: `src/nextlife/services/deals.py:53-59`.

Снятие listings при продаже делает `unpublish.py` (`initiate_unpublish_on_sale` → Job `UNPUBLISH_LISTING` → `execute_unpublish_tasks` → `listing.status="ARCHIVED"`), а `archive_item_with_confirmation` переводит ACTIVE listings в ARCHIVED + item в ARCHIVED (`deals.py:70-130`). — Источник: `unpublish.py:33-129`, `deals.py:70-130`.

### B6. Удаление item (не lot)

**API-маршрута удаления item нет** (дамп: только `DELETE /api/lots/{code}`, нет `DELETE /api/items/{code}`). Физическое удаление item существует только в `_prune_wizard_drafts` (удаление старейших DRAFT-черновиков при превышении лимита):

```python
for item in _wizard_drafts(session)[:overflow]:
    session.execute(delete(ItemPhoto).where(ItemPhoto.item_code == item.code))
    session.execute(delete(Lot).where(Lot.item_id == item.code))
    session.delete(item)
```
— Источник: `src/nextlife/services/wizard_draft.py:49-52`; дамп маршрутов `.a/reports/routes-dump3.txt`.

### B7. `delete_photo_and_derived` — файлы на диске

**Функция НЕ удаляет файлы с диска** — она удаляет строки БД и возвращает список путей; удаление файлов делает вызывающий после commit:

```python
def delete_photo_and_derived(session: Session, code: str, photo_id: str) -> list[str]:
    """... The caller must commit and only then remove the returned files from
    disk: unlinking before commit leaves DB rows without files when the
    transaction rolls back ..."""
    ...
    files: list[str] = []
    for level in reversed(levels):
        for p in level:
            files.append(p.path)
            session.delete(p)
            session.flush()
    return files
```
— Источник: `src/nextlife/media/ops.py:33-77`.

**Шаблон пути фото:** `<media_dir>/orig_{sha256[:16]}_{original_filename}`, где `media_dir = <data_dir>/media` (production: `/var/lib/nextlife/media`). Sanitized-копия: `clean_{sha256[:16]}.{ext}`. — Источник: `src/nextlife/media/ingest.py:84,101`; `src/nextlife/core/config.py:416-422`.

## C. Мастер

### C1. Какой мастер открывается из UI

| Пункт | Ссылка | Мастер | Источник |
|---|---|---|---|
| Кнопка «+» в шапке | `/ui/lot-wizard/search` | Живой (lot-wizard) | `base.html:22` |
| Нижняя навигация «Создать» | `/ui/create` | Страница создания (ссылка на живой) | `base.html:49` |
| Меню «Создать» | `/ui/create` | Страница создания | `base.html:74` |
| Кнопка на /ui/create «➕ Новый лот (мастер)» | `/ui/lot-wizard/search` | Живой | `create.html:10` |
| Кнопка «Создать предмет» на /ui/items | `/ui/wizard` | **Легаси** (wizard.html) | `items.html:52` |

**Вывод:** из основной навигации (шапка, нижняя панель, меню) открывается **живой мастер** (`/ui/lot-wizard/search`). **Ссылка на легаси `/ui/wizard` есть** — кнопка «Создать предмет» на странице /ui/items (`items.html:52`). — Источник: `templates/web/base.html:22,49,74`, `create.html:10`, `items.html:52`.

### C2. Шаги живого мастера

**Шаг 1 — поиск** (`/ui/lot-wizard/search`):
- Маршруты: `GET /ui/lot-wizard/search` (`routes_lots.py:516`), `POST /ui/lot-wizard/search` (`routes_lots.py:531`).
- Поля формы: `query` (Form), `lang` (Form) — `routes_lots.py:532-534`.
- Запись не создаётся. Создание черновика: `POST /ui/lot-wizard/draft` (`routes_lots.py:848`) → `create_draft_from_selection` → создаёт Item+Lot (если нет `item_code`) или дополняет существующий. Статус `DRAFT`. — Источник: `routes_lots.py:516-531,848-877`; `wizard_draft.py:59-100`.

**Шаг 2 — фото и текст** (`/ui/lot-wizard/text`):
- Маршруты: `GET /ui/lot-wizard/text` (`routes_lots.py:775`), `POST /ui/lot-wizard/text` (`routes_lots.py:818`).
- Поля: `item_code`, `title`, `notes` — `routes_lots.py:819-822`.
- Пишется: `item.title` (`routes_lots.py:836`), `item.notes` (`routes_lots.py:839`), `attrs["wizard_generated_text"]` (`wizard_textgen.save_generated`). Запись Item уже существует (создана на шаге 1). — Источник: `routes_lots.py:775-846`; `wizard_textgen.py:35-45`.
- Фото: `POST /ui/lot-wizard/{code}/photos/upload` (`routes_lots.py:880`).

**Шаг 3 — параметры (frames)** (`/ui/lot-wizard/finalize`):
- Маршруты: `GET /ui/lot-wizard/finalize` (`routes_lots.py:900`), `POST /ui/lot-wizard/finalize/frame/generate` (`routes_lots.py:942`), `POST /ui/lot-wizard/finalize/frame/save` (`routes_lots.py:965`).
- Поля (save): `item_code`, `mp_key`, `title`, `description`, `category`, `price`, `currency`, `main_photo` — `routes_lots.py:966-973`.
- Пишется: `attrs["wizard_frames"][mp_key]` (`wizard_localize.save_frame`). Статус фрейма `approved`. — Источник: `routes_lots.py:900-1000`; `wizard_localize.py:326-345`.

**Шаг 4 — публикация** (`/ui/lot-wizard/publish`):
- Маршруты: `GET /ui/lot-wizard/publish` (`routes_lots.py:1013`), `POST /ui/lot-wizard/publish/dry-run` (`routes_lots.py:1062`), `POST /ui/lot-wizard/publish/bazos` (`routes_lots.py:1085`).
- Dry-run: пишется `attrs["wizard_dry_run_report"]` (`wizard_publish_dry_run.py:91`).
- Bazoš live: вызывает `bazos_publish_listing` (см. C4). — Источник: `routes_lots.py:1013-1169`.

### C3. Может ли мастер принять существующий code

**Да.** Все шаги принимают `item_code`:
- `/ui/lot-wizard/text?item_code=...` (`routes_lots.py:777`)
- `/ui/lot-wizard/finalize?item_code=...` (`routes_lots.py:902`)
- `/ui/lot-wizard/publish?item_code=...` (`routes_lots.py:1015`)
- `create_draft_from_selection` принимает `item_code` и дополняет существующий Item (`wizard_draft.py:75-83`).
- Страница /ui/create показывает черновики с `resume_url` (`routes_lots.py:509-510`).
— Источник: `routes_lots.py:777,902,1015,509-510`; `wizard_draft.py:75-83`.

### C4. Публикация в мастере и listing

**Dry-run:** запись `Listing` **не создаётся** — только `attrs["wizard_dry_run_report"]` (`wizard_publish_dry_run.py:91`).

**Bazoš live (`POST /ui/lot-wizard/publish/bazos`):** запись `Listing` **НЕ создаётся**. Функция `api_lot_wizard_publish_bazos` вызывает `bazos_publish_listing(...)` и делает редирект — в БД запись Listing не пишется:

```python
result = bazos_publish_listing(
    title=frame.get("title", ""),
    description=frame.get("description", ""),
    price=str(frame.get("price", "")),
    category=frame.get("category", "notebook"),
)
if result.get("ok"):
    notice = "publish_done"
    ...
return RedirectResponse(url=f"/ui/lot-wizard/publish?item_code={item_code}&notice={notice}...")
```
— Источник: `src/nextlife/api/routes_lots.py:1140-1169`.

**Реальное создание Listing** происходит в `publish.py` (`publish_item`), который вызывается из `POST /api/lots/{code}/publish` и `POST /api/oc/items/{code}/publish`. Статус `PUBLISHED`:

```python
listing = Listing(
    id=listing_id, item_code=item.code, marketplace_key=mp_key,
    external_id=pub_result.external_id, external_url=str(ext_url).strip(),
    price=price_val, currency=lot.currency, price_base=price_val,
    status="PUBLISHED", published_at=datetime.now(UTC), publish_evidence=evidence,
)
```
— Источник: `src/nextlife/services/publish.py:197-207`.

**Вывод:** публикация в живом мастере (Bazoš) **не создаёт запись Listing** в БД. — Источник: `routes_lots.py:1140-1169`.

## D. Текст и язык

### D1. Цепочка от русского описания до текста на площадке

1. **Шаг 2** — `wizard_textgen.generate_description` генерирует **русский** title+text (prompt «…на русском языке…»), сохраняет в `attrs["wizard_generated_text"]` — `wizard_textgen.py:69-152`.
2. **Шаг 3** — `wizard_localize.localize_frame` вызывает `localize_listing_text`, который **переводит** title+description на язык площадки LLM'ом и определяет категорию — `wizard_localize.py:157-220`.
3. Переведённый текст сохраняется в `attrs["wizard_frames"][mp_key]["title"/"description"]` — `wizard_localize.py:326-345`.

**Перевод: ЕСТЬ.** Промпт (`wizard_localize.py:170-186`):
```
Сформируй объявление на {lang} языке для площадки «{mp_cfg.title}»
по исходному тексту и названиям товаров из эталонных объявлений.
ЗАГОЛОВОК: название товара + ключевые характеристики ... до 60 символов.
ОПИСАНИЕ: переведи исходное описание на {lang} язык (2-3 предложения).
КАТЕГОРИЯ: самая конкретная категория площадки ...
Верни только JSON: {"title": "...", "description": "...", "category": "..."}
```
Язык цели — из `_COUNTRY_LANGUAGE` (CZ→чешский, PL→польский, DE/AT→немецкий) — `wizard_localize.py:31-37`. Без LLM — fallback на исходный текст (не выдуманный перевод) — `wizard_localize.py:166-169`.
— Источник: `src/nextlife/services/wizard_textgen.py:69-152`; `src/nextlife/services/wizard_localize.py:31-37,157-220,326-345`.

### D2. Какой текст подставляется в форму Bazoš

В `api_lot_wizard_publish_bazos` (`routes_lots.py:1140-1153`) в `bazos_publish_listing` передаются `frame.get("title")` и `frame.get("description")` — то есть **локализованный (переведённый) текст** из утверждённого фрейма `attrs["wizard_frames"]["bazos_cz"]`.

Фрагмент `bazos_publish_listing` (`bazos_publish.py:375-395`):
```python
# Title
page.fill("form[action='/insert.php'] input[name='nadpis']", title)
# Description
page.fill("form[action='/insert.php'] textarea[name='popis']", description)
# Price
page.fill("form[action='/insert.php'] input[name='cena']", price)
```
Поля: `nadpis` (заголовок), `popis` (описание), `cena` (цена), `category` (select), `lokalita`, `jmeno`. — Источник: `src/nextlife/services/bazos_publish.py:375-395`; `src/nextlife/api/routes_lots.py:1140-1153`.

### D3. Перечень ключей `item.attrs`

| Ключ | Назначение | Источник |
|---|---|---|
| `wizard_selected` | Выбранные результаты поиска (шаг 1) | `wizard_draft.py:77` |
| `wizard_status` | Маркер «params_approved» (шаг 3) | `wizard_finalize.py:55` |
| `wizard_marketplaces` | Список площадок (шаг 3) | `wizard_finalize.py:51` |
| `wizard_publish_at` | Время публикации (шаг 3) | `wizard_finalize.py:53` |
| `wizard_repost` | Флаг репоста (шаг 3) | `wizard_finalize.py:54` |
| `wizard_generated_text` | Сгенерированный RU title/text/mode (шаг 2) | `wizard_textgen.py:36` |
| `wizard_frames` | Локализованные фреймы по площадкам (шаг 3) | `wizard_localize.py:335` |
| `wizard_dry_run_report` | Отчёт dry-run (шаг 4) | `wizard_publish_dry_run.py:91` |
| `final_text` | Финальный утверждённый текст | `routes.py:451`; `lot_edit.py:145` |
| `generated_description` | Чешский черновик описания (fallback) | `draft_text.py:40` |
| `listing_text_cs` | Чешский текст (fallback) | `draft_text.py:41` |
| `description` | Описание (fallback) | `draft_text.py:42` |
| `notes` | Заметки (fallback) | `draft_text.py:36` |
| `pickup_allowed` | Разрешена ли самовывоз | `sell.py:75` |
| `photo_order` | Порядок фото | `routes_photos_ops.py:60,111-112` |
| `cover_photo_id` | Обложка | `routes_photos_ops.py:109` |

— Источник: grep `attrs[`/`attrs.get` по `src/nextlife`.

---

## E. Шлюз OpenClaw

### E1. Дамп маршрутов `/api/oc/*`

| Метод | Путь | Параметры | Вызываемая функция |
|---|---|---|---|
| GET | `/api/oc/status` | — | `oc_status` |
| POST | `/api/oc/items` | body: title, category, price, currency, condition | `create_item_and_lot` |
| GET | `/api/oc/items/{code}` | — | `oc_item_show` |
| POST | `/api/oc/items/{code}/research` | query (opt) | `run_research` |
| GET | `/api/oc/items/{code}/research/report` | — | `build_research_report` |
| POST | `/api/oc/items/{code}/draft` | — | `build_draft` |
| POST | `/api/oc/items/{code}/publish` | body: dry_run | `publish_item` |
| GET | `/api/oc/items/{code}/listings` | — | `oc_publish_status` |
| POST | `/api/oc/items/{code}/photos` | files, role, ai_generated, vlm_verdict | `ingest_photo`+pipeline |
| POST | `/api/oc/items/{code}/media-build` | — | `assemble_marketplace_kit` |
| POST | `/api/oc/search` | body: platform, query, limit | `AndroidBridge.search` |
| POST | `/api/oc/wizard/search` | body: query, marketplaces, limit | `search_across_marketplaces` |
| POST | `/api/oc/wizard/text` | body: item_code, selected, notes | `generate_description` |
| POST | `/api/oc/wizard/draft` | body: selected, item_code, notes, category | `create_draft_from_selection` |
| POST | `/api/oc/wizard/finalize` | body: item_code, price, currency, marketplaces, publish_at, repost | `finalize_draft_params` |
| POST | `/api/oc/wizard/publish-dry-run` | body: item_code | `publish_dry_run` |
| GET | `/api/oc/outbox` | — | `pull_outbox` |
| POST | `/api/oc/outbox/{id}/ack` | body: delivery_evidence | `ack_outbox` |
| POST | `/api/oc/inbox` | payload | `handle_inbox_push` |
| GET | `/api/oc/inbox/pending` | — | `get_pending_inbox` |
| POST | `/api/oc/deals/close` | body: code | `archive_item_with_confirmation` |
| POST | `/api/oc/canaries/run` | body: key (opt) | `run_search_canary` |
| GET | `/api/oc/canaries` | — | `oc_canaries_status` |

Аутентификация: `check_openclaw_auth` (Bearer `NEXTLIFE_OPENCLAW_TOKEN`) — `routes_openclaw.py:37-49`. — Источник: `src/nextlife/api/routes_openclaw.py`, `src/nextlife/api/routes_openclaw_ops.py`.

### E2. Полный текст SKILL.md

```markdown
---
name: nextlife
description: OpenClaw skill for buying and selling second-hand goods via marketplaces.
commands:
  - nextlife status
  - nextlife item create --title <title> --category <category>
  - nextlife item show --code <code>
  - nextlife research start --code <code>
  - nextlife research report --code <code>
  - nextlife media ingest --code <code> --files <paths...>
  - nextlife media build --code <code>
  - nextlife draft build --code <code>
  - nextlife publish --code <code>
  - nextlife publish status --code <code>
  - nextlife outbox pull
  - nextlife outbox ack --id <id> --delivery-evidence <json>
  - nextlife inbox push
  - nextlife inbox pending
  - nextlife deal close --code <code>
  - nextlife canaries run
  - nextlife canaries status
exit_codes:
  0: Success
  2: Input error
  3: External system unavailable
  4: Owner decision required
  5: Internal error
---

# NextLife Skill for OpenClaw

## Description
NextLife automates selling and buying second-hand items across multiple
European marketplaces (Bazoš, Sbazar, Aukro, etc.). The CLI is a thin HTTP
client to the NextLife service on 127.0.0.1 (default port 8081),
authenticated with `NEXTLIFE_OPENCLAW_TOKEN` (injected by the skill runtime;
never ask the owner for the token value, never print it).

## Output Contract
- All CLI output to `stdout` is strictly JSON.
- Diagnostic logs go to `stderr`.
- Exit codes strictly adhere to the specification.

## Draft and approval workflow
- `nextlife draft build --code <code>` returns the draft and a `web_url`
  (e.g. `/ui/draft/ABC`). **Send that link to the owner** — approval is an
  owner action in the web UI only; the CLI cannot approve.
- `nextlife publish --code <code>` requires a current approval: if the draft
  is not approved or was modified after approval, the API returns 409 and the
  CLI exits with code 4. Then send the owner the `web_url` again and ask to
  approve the current draft before retrying.
```
— Источник: `SKILL.md` (repo root).

### E3. Операции только в UI (отсутствуют в /api/oc)

| Операция | Маршрут (UI/API) |
|---|---|
| Редактирование лота | `PATCH /api/lots/{code}` |
| Удаление лота | `DELETE /api/lots/{code}` |
| Фото: rotate/crop/clean-bg/remove-dust/defect/cover/reorder | `POST /api/items/{code}/photos/*` |
| Утверждение черновика | `POST /ui/draft/{item_code}/approve` |
| Опции доставки | `GET/POST /api/lots/{code}/shipping-options` |
| Превью на площадке | `GET /ui/items/{code}/preview/{marketplace_key}` |
| Публикация (live) | `POST /api/lots/{code}/publish` |

**Вывод:** `/api/oc` не имеет операций редактирования/удаления лота, фото-ретуши, утверждения черновика, опций доставки и live-публикации (кроме `publish_item` с dry_run). — Источник: дамп `.a/reports/routes-dump3.txt`.

## F. Интерфейс

### F1. Дамп всех маршрутов приложения

Всего **122 маршрута**. Команда:
```
cd /path/to/NextLife && NEXTLIFE_DSN="postgresql+psycopg2://dummy:dummy@localhost/dummy" \
PYTHONPATH=src .venv/bin/python -c "
from nextlife.api.main import create_app
app = create_app()
out=[]
def walk(routes):
    for r in routes:
        sub = getattr(r,'original_router',None)
        p = getattr(r,'path',None)
        m = sorted(getattr(r,'methods',None) or [])
        if sub is not None and hasattr(sub,'routes'): walk(sub.routes)
        elif p is not None: out.append((p, ','.join(m)))
walk(app.routes)
for p,m in sorted(set(out)): print(m.ljust(20), p)
"
```
Полный вывод — `.a/reports/routes-dump3.txt`. Ключевые группы:
- `/ui/*` — 58 маршрутов (web UI)
- `/api/oc/*` — 23 маршрута (OpenClaw)
- `/api/*` — 17 маршрутов (web API)
- `/api/publish/bazos/*` — 5 (Bazoš login/recon/publish)
- `/api/recon/bazos*` — 3 (рекон)
- `/api/threads/*` — 2
- `/api/deals/*` — 3
- `/api/dashboard` — 1
- `/media`, `/static` — 2 (mount)
- `/`, `/ui`, `/health`, `/healthz`, `/status`, `/docs`, `/openapi.json`, `/redoc` — 8

— Источник: `.a/reports/routes-dump3.txt` (TOTAL 122).

### F2. Нижняя навигация и меню

**Нижняя навигация** (`base.html:43-67`):
| Подпись | Ссылка | file:line |
|---|---|---|
| Дашборд | `/ui/dashboard` | `base.html:44` |
| Лента | `/ui/items` | `base.html:47` |
| Создать | `/ui/create` | `base.html:49` |
| Входящие | `/ui/inbox` | `base.html:52` |
| Меню | (кнопка) | `base.html:55` |

**Меню** (`base.html:69-95`):
| Подпись | Ссылка | file:line |
|---|---|---|
| Дашборд | `/ui/dashboard` | `base.html:70` |
| Лента | `/ui/items` | `base.html:73` |
| Создать | `/ui/create` | `base.html:74` |
| Входящие | `/ui/inbox` | `base.html:77` |
| Сделки | `/ui/archive` | `base.html:80` |
| Настройки | `/ui/settings` | `base.html:83` |

**Шапка** (`base.html:22`): кнопка «+» → `/ui/lot-wizard/search`.

### F3. Мёртвые маршруты `/ui/*` (не связаны ни с одной ссылкой из шаблонов)

Сравнение дамба маршрутов (`.a/reports/routes-dump3.txt`) со всеми ссылками `/ui/` в шаблонах (`.a/reports/ui-refs.txt`):

| Маршрут | Статус |
|---|---|
| `GET /ui/marketplaces` | **МЁРТВЫЙ** — нет ни одной ссылки в шаблонах |
| `POST /ui/lots/create` | **МЁРТВЫЙ** — нет ни одной ссылки в шаблонах |

Остальные `/ui/*` маршруты либо имеют прямую ссылку, либо вызываются динамически через JS (fetch) — их нельзя считать мёртвыми по статическому анализу. — Источник: `.a/reports/routes-dump3.txt`, `.a/reports/ui-refs.txt`; `grep -rn "ui/marketplaces" templates/` → NONE; `grep -rn "ui/lots/create" templates/` → NONE.

---

## G. Доставка

### G1. Вес и габариты предмета

**РЕАЛИЗОВАНО** (колонки в `item`):
- `item.weight_g` Integer — `core.py:59`
- `item.dim_l_mm` Integer — `core.py:60`
- `item.dim_w_mm` Integer — `core.py:61`
- `item.dim_h_mm` Integer — `core.py:62`
- `item.packed_weight_g` Integer — `core.py:65`

Используется в `shipping_options.py` (`item.packed_weight_g or item.weight_g`) — `shipping_options.py:202,246`. — Источник: `src/nextlife/storage/models/core.py:59-65`.

### G2. DeliveryOption при публикации на Bazoš

**НЕ ИСПОЛЬЗУЕТСЯ.** `bazos_publish_listing` заполняет поля `nadpis`, `popis`, `cena`, `category`, `lokalita`, `jmeno` — поля доставки/тарифа в форму Bazoš не передаются. `DeliveryOption`/`ShippingService` не вызываются. — Источник: `src/nextlife/services/bazos_publish.py:375-395`.

---

## H. Прочее

### H1. Перечень значений `action` в audit_event

44 уникальных значения (grep `action="..."` по `src/nextlife`):

`PUBLISH_ATTEMPT`, `COD_DISPATCH_RISK_CALCULATED`, `COD_RETURNED_RECORDED`, `DISPUTE_OPENED`, `MEETING_PROPOSED`, `BUY_CANDIDATE_DISCOVERED`, `CANDIDATES_EVALUATED`, `LOT_DELETED`, `LOT_EDITED`, `ARCHIVE_MISMATCH`, `BUYER_SELLER_INQUIRY_SENT`, `MESSAGE_ESCALATED`, `ESCALATION_COMMAND_EXECUTED`, `PAYMENT_CONFIRMED`, `PAYMENT_SCREENSHOT_REJECTED_AS_PROOF`, `DISPATCH_EVIDENCE_SUBMITTED`, `DEAL_DISPATCHED`, `LISTING_BUMPED`, `LISTING_RELISTED`, `PICKUP_ADDRESS_RELEASED`, `TRACKING_NUMBER_RELEASED`, `LOT_RESERVED`, `RESERVATION_EXPIRED`, `UNPUBLISH_INITIATED`, `UNPUBLISH_FAILED`, `UNPUBLISH_DEADLINE_EXCEEDED`, `LOT_SOLD`, `LOT_PUBLISH_ATTEMPT`, `INSPECTION_PROTOCOL_ENQUEUED`, `PII_PURGED`, `MONTHLY_SUMMARY_GENERATED`, `HANDOVER_PROTOCOL_GENERATED`, `FRAUD_SUSPECTED`, `PHOTOS_UPLOADED`, `PHOTO_DELETED`, `PHOTOS_REORDERED`, `PHOTO_SET_COVER`, `PHOTO_ROTATED`, `PHOTO_CROPPED`, `PHOTO_DUST_REMOVED`, `PHOTO_BACKGROUND_CLEANED`, `PHOTO_DEFECT_TOGGLED`, `PHOTO_VARIANTS_REGENERATED`, `UNSAFE_URL_BLOCKED`.

— Источник: grep `action\s*=\s*["'][A-Z_]+["']` по `src/nextlife` (49 совпадений, 44 уникальных значения).

### H2. Лимит 5 черновиков

**Где проверяется:** `wizard_draft.py:25` (`MAX_WIZARD_DRAFTS = 5`), `_prune_wizard_drafts` (`wizard_draft.py:42-56`).

**На что распространяется:** Item DRAFT — `kind=SELL`, `status=DRAFT`, `attrs.has_key("wizard_selected")` (`wizard_draft.py:30-36`). AWAITING_APPROVAL **исключён** (примечание в docstring `wizard_draft.py:23-24`). При превышении лимита удаляются старейшие (ItemPhoto + Lot + Item) — `wizard_draft.py:49-52`. — Источник: `src/nextlife/services/wizard_draft.py:25,30-36,42-56`.

### H3. Блок «Особенности» на карточке предмета

**Источник данных:** `item.defects` (JSONB) → `split_defects_and_features` (`core/defects.py:37-45`) разбивает на `real_defects` + `features` (позитивные формулировки по маркерам `POSITIVE_MARKERS`). В шаблон передаётся `features` — `routes.py:295,305`.

```python
real_defects, features = split_defects_and_features(item.defects)
...
"features": features,
```
— Источник: `src/nextlife/api/routes.py:295,305`; `src/nextlife/core/defects.py:37-45`; `templates/web/item_detail.html:198-202`.

```html
{% if item.features %}
<div class="card" id="features-card" data-testid="item-features">
  <div class="card-title">Особенности</div>
  {% for d in item.features %}
  ...
```
— Источник: `templates/web/item_detail.html:198-202`.
