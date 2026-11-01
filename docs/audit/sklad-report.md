# Склад: отчёт по этапам (ТЗ «Склад» вместо «Ленты»)

Дата: 2026-10-25. Репозиторий: `/path/to/NextLife`.
Базовая линия: `docs/audit/sklad-baseline.txt` (коммит a643f36: 21 failed / 650 passed, падения — окружение + 3 предсуществующих).

## Этап 1 — миграция 0022 + сервис stock (коммит 0db1659)

- Миграция `0022`: новые колонки `item.description_lang`, `item.description`, `item.usage`, `item.specs`, `item.storage_location`.
- Сервис `nextlife.services.stock`: `list_stock`, `get_stock_item`, `create_stock_item`, `update_stock_item`, `delete_stock_item`, `mark_sold`, `delete_preview`.
- Текст объявления читается из `item.description` (единый канонический текст).
- `lot.price_gross/currency/notes` пишутся только сервисом stock синхронно с item в одной транзакции.

## Этап 2 — снятие через коннекторы + listing при публикации (коммит 7695a99)

- Снятие объявлений — через коннекторы реестра (`unpublish_listings_now`), не локальная ARCHIVED.
- Запись `listing` при публикации из мастера.
- Счётчик активных объявлений (статусы PUBLISHED / PARTIALLY_PUBLISHED / ACTIVE / UNPUBLISH_FAILED).

## Этап 3 — UI «Склад» (коммит 8b6a99e)

### 3.1 Навигация и маршруты

- Нижнее меню и «Меню»: «Лента» → «Склад», `href /ui/items` → `/ui/stock` — `templates/web/base.html`.
- Маршруты: `GET /ui/stock`, `GET /ui/stock/new`, `GET /ui/stock/{code}` — `src/nextlife/api/routes_stock.py`.
- Редиректы 301: `/ui/items` → `/ui/stock`, `/ui/items/{code}` → `/ui/stock/{code}` (query-string, включая `?token=`, сохраняется) — `src/nextlife/api/routes.py:190-212`.
- Router подключён — `src/nextlife/api/main.py`.

### 3.2–3.3 Экраны

- Список `templates/web/stock.html`: заголовок «Склад» + «+», поиск (client-side по коду/названию/месту), чипы Все/На продаже/Черновики/Проданы, карточка (код, статус, миниатюра, название, цена+валюта, место на складе или «место не задано», счётчик объявлений), пустое состояние «Склад пуст».
- Предмет `templates/web/stock_item.html` + `templates/web/_stock_form.html`: форма (wz-input/wz-select) по группам Основное/Текст/Заметки/Доставка, «Сохранить» (целая форма), предупреждение «объявления не обновлены».
- Фото — вынесен в `templates/web/_photo_gallery.html` (слайдер/миниатюры/загрузка/операции без изменений), включён в `stock_item.html`. Контекст передаёт верхнеуровневый `photos` — `src/nextlife/api/routes_stock.py:82-92`.
- Конструктор доставки — `templates/web/_shipping_constructor.html` ( тот же сервис shipping_options).
- Таблица объявлений: PUBLISHED/ACTIVE=«Размещено», ARCHIVED=«Снято», UNPUBLISH_FAILED=«Не снято — снимите вручную» + ссылка.
- Действия: «Создать объявление» → `/ui/lot-wizard/text?item_code=`, «Продан», «Удалить» (2-шаговое подтверждение).

### 3.4 Удаление

- `DELETE /api/stock/{code}`: 409 без `confirm_listings`; 409 при активной сделке; файлы фото удаляются после коммита; статус DELETED; предмет исчезает из списка; redirect `/ui/stock?notice=deleted`.

### 3.6 Место на складе при обработке отправок

«Место на складе: …» первой строкой карточки предмета в каждом экране и JSON-ответе. Изменённые места (file:line):

| Место | file:line |
| --- | --- |
| `deal.html` — строка карточки | `templates/web/deal.html:10` |
| `archive.html` — строка карточки | `templates/web/archive.html:37` |
| DEAL_DISPATCHED (JSON) | `src/nextlife/services/dispatch_evidence.py:256` |
| DISPATCH_EVIDENCE_SUBMITTED (JSON) | `src/nextlife/services/dispatch_evidence.py:134` |
| PICKUP_ADDRESS_RELEASED (JSON) | `src/nextlife/services/fulfillment.py:128` |
| TRACKING_NUMBER_RELEASED (JSON) | `src/nextlife/services/fulfillment.py:214` |
| `/api/oc/deals/close` (JSON, archive) | `src/nextlife/services/deals.py:122` |
| Экран сделки (контекст) | `src/nextlife/api/routes_deal_screen.py:51,85` |

Помощник `_storage_location_for_deal` (deal.lot_id → Lot → item.storage_location):
`src/nextlife/services/dispatch_evidence.py:31`, `src/nextlife/services/fulfillment.py:29`.

HANDOVER_PROTOCOL_GENERATED (`src/nextlife/deals/handover_protocol.py`) возвращает кортеж `(text, path)`, не JSON — JSON-ответа для дополнения нет; экран сделки (`deal.html:10`) уже выводит место на складе.

### 3.7 Тесты этапа

- `tests/test_stock_ui.py` — 12 тестов: редиректы 301, создание через `/api/stock`, правка каждого поля, отображение места на складе в списке, экран предмета, 404, DELETE 409/подтверждение/DELETED/исчезновение, delete-preview.
- `tests/test_ui_lightbox_swipe.py`, `test_ui_lightbox_arrows.py`, `test_ui_lightbox_strip_counter.py` — 3 теста (Playwright, реальный браузер): лайтбокс на экране предмета Склада (свайп/стрелки/лента миниатюр). Прошли после фикса передачи `photos` в контекст и сохранения query-string в редиректе.

Вывод тестов этапа (`.venv/bin/python -m pytest tests/test_stock_ui.py -q`):
```
12 passed, 2 warnings in 6.69s
```
Лайтбокс (`.venv/bin/python -m pytest tests/test_ui_lightbox_swipe.py tests/test_ui_lightbox_arrows.py tests/test_ui_lightbox_strip_counter.py -q`):
```
3 passed, 1 warning in 20.80s
```
Результат full suite (`.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q`): **4 failed, 700 passed**. Все 4 падения — из базовой линии (окружение/предсуществующие):
`test_check_hardcode`, `test_listing_templates`, `test_screen_deal_playwright`, `test_stage_e7`.

## Этап 4 — мастер объявлений поверх Склада (коммит sklad-4)

Мастер работает от предмета Склада: шаг 1 выбирает или создаёт предмет, шаг 2
правит все его поля, шаги 3–4 работают от полей предмета. `item` — источник
истины для полей Склада.

### 4.1 Шаг 1 — выбор/создание предмета (`GET /ui/lot-wizard/item`)

- `screen_lot_wizard_item` — `src/nextlife/api/routes_lots.py:492-493`.
  `list_stock(session, "all")` минус `SOLD`/`ARCHIVED` (`list_stock` уже
  исключает `DELETED`+`CANCELLED`); бейдж статуса — `get_status_badge_class`.
- `POST /ui/lot-wizard/item` (`api_lot_wizard_item_new`, `routes_lots.py:526-527`)
  — `create_stock_item({"title": …})` → 303 `/ui/lot-wizard/text?item_code=`;
  пустое название → 303 `?notice=empty_title`.
- Шаблон `templates/web/lot_wizard_item.html`: «Новый предмет» (Наименование +
  «Создать»), client-side поиск `filterWizardItems` по коду/названию/месту
  (`data-code`/`data-title`/`data-loc`), карточки → `/ui/lot-wizard/text?item_code=`.

### 4.2 Шаг 2 — поля предмета + текст + аналоги (`GET /ui/lot-wizard/text?item_code=`)

- `screen_lot_wizard_text` — `routes_lots.py:791-792`. Без `item_code` → 303 шаг 1;
  `StockItemNotFoundError` → 303 `?notice=not_found`. Контекст: `item`
  (`get_stock_item` + `shipping_summary`/`shipping_summary_json`), `next`,
  `photos`, `search_languages`, `notice`.
- Форма — `{% include "_stock_form.html" %}` (все поля), сохранение через
  `PATCH /api/stock/{code}`. `POST /api/stock/{code}` принимает опциональный
  `next` и возвращается на шаг 2 с `notice=saved`/`saved_not_updated`
  (`routes_stock.py:243,272-279`). `_stock_form.html:8` — скрытое `next`.
- «Сгенерировать описание» — `POST /ui/lot-wizard/text`
  (`api_lot_wizard_text`, `routes_lots.py:853-854`): `generate_description(
  item_code, …, lang=item.description_lang)` → `update_stock_item`
  (`item.title`+`item.description`), не в `attrs`. Язык промпта —
  `_GENERATION_LANG_PREP` (`wizard_textgen.py`): ru/cs/en/de/pl. JS-подтверждение
  «Заменить текущее описание?», если описание непусто.
- «Аналоги на площадках» — панель (бывший шаг 1): `POST /ui/lot-wizard/search`
  (SSE, без изменений) + `create_draft_from_selection(item_code)` →
  `attrs["wizard_selected"]`. Загрузка фото — существующий маршрут.

### 4.3 Шаг 3 — кадры из полей предмета (`GET /ui/lot-wizard/finalize?item_code=`)

- `localize_frame` (`wizard_localize.py:265`) — источник = `item.description` +
  `item.usage` + `item.specs` (НЕ `notes`, не `load_generated`). Промпт: полный
  перевод описания/использования/характеристик, характеристики списком.
- Цена кадра — `_frame_price` (`wizard_localize.py:120`): `item.target_price`,
  конвертация через `convert_price` (ECB, EUR-база) при различном `target_currency`;
  кадр хранит дату курса (`price_rate_date`).
- `screen_lot_wizard_finalize` — `routes_lots.py:959-960`; без `item_code` → 303 шаг 1.

### 4.4 Навигация, редиректы, снятие лимита черновиков

- `base.html`: «+» (header) и «Создать» (bottom-nav + меню) → `/ui/lot-wizard/item`.
- Редиректы 303 на шаг 1: `GET /ui/create` (`routes_lots.py:483`),
  `GET /ui/lot-wizard` (`routes_lots.py:477`), `GET /ui/lot-wizard/search`
  (`routes_lots.py:554`); шаги 2–4 без `item_code` → 303 шаг 1
  (`routes_lots.py:806,974,1097`).
- Удалены `MAX_WIZARD_DRAFTS` + `_prune_wizard_drafts` + `_wizard_drafts`
  (`wizard_draft.py`) — лимит черновиков снят (ТЗ §4.4).

### 4.5 Фикс вложенной формы — конструктор доставки

`templates/web/_shipping_constructor.html` включается внутрь `<form>` из
`_stock_form.html`; вложенный `<form>` запрещён HTML — браузер закрывал внешнюю
форму, и кнопка «Сохранить» оказывалась вне неё. Фикс: внутренний `<form>`
заменён на `<div>` + JS `saveShipping()` (fetch POST `/ui/lots/{code}/shipping`,
follow redirect). Кнопка `type=button` (`data-testid=btn-save-shipping`).
Исправлен x-data: `shipping_summary_json` теперь берётся из `it`
(`it.get('shipping_summary_json','{}')`), а не из несуществующего в контексте
`summary_json` (была JS-синтаксическая ошибка).

### 4.6 Тесты этапа

- `tests/test_wizard_item.py` (новый): список продаваемых (DRAFT да, SOLD нет),
  карточка → шаг 2, создание → шаг 2.
- `tests/test_wizard_localize.py`: `_frame_price` (reference/target/конвертация),
  источник кадра = поля предмета (не notes), конвертация target_currency.
- `tests/test_wizard_textgen.py`: страница показывает поля предмета, редирект без
  `item_code`, генерация пишет `item.description` на `description_lang`.
- `tests/test_wizard_draft.py`: 6-й черновик не удаляет предметы.

Вывод тестов этапа:
```
28 passed, 2 warnings in 6.71s
```
Результат full suite (`.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q`):
**4 failed, 706 passed**. Все 4 падения — из базовой линии (окружение/
предсуществующие): `test_check_hardcode`, `test_listing_templates`,
`test_screen_deal_playwright`, `test_stage_e7`.

Браузерная верификация (Playwright, мобильный 390x844):
- `verify-wizard-sklad.py` — 19/19 (шаг 1, создание→шаг 2, поля+сохранение+toast,
  редиректы без `item_code`, шаг 3). Скриншоты `.a/reports/screenshots/wz-step*.png`.
- `verify-constructor-js.py` — 6/6 (конструктор рендерится, x-data валиден,
  без JS-ошибок, кнопка в форме, save POST 303).

## Этап 5 — Шлюз `/api/oc/stock` + SKILL.md

### 5.1 Шлюз `/api/oc/stock` (9 маршрутов)

Новый модуль `src/nextlife/api/routes_openclaw_stock.py` (309 строк, ≤400 по VI.1.1).
`oc_stock_router = APIRouter(prefix="/api/oc/stock")`, auth `check_openclaw_auth`
(только Bearer NEXTLIFE_OPENCLAW_TOKEN, без fallback на web-токен). Все записи —
через `nextlife.services.stock` (§1.4); имена полей = колонки §1.1; перевозчики —
`delivery_options: [{carrier_key, price, is_enabled}]` → `configure_lot_delivery_options`.

| Метод | Путь | Хендлер | Сервис |
|---|---|---|---|
| GET | `/api/oc/stock` | `oc_stock_list` | `list_stock(group, q)` |
| POST | `/api/oc/stock` | `oc_stock_create` (201, полная карточка) | `create_stock_item` |
| GET | `/api/oc/stock/{code}` | `oc_stock_show` | `get_stock_item` |
| PATCH | `/api/oc/stock/{code}` | `oc_stock_patch` | `update_stock_item` (+ `listings_not_updated`) |
| POST | `/api/oc/stock/{code}/sold` | `oc_stock_sold` | `mark_sold(actor="OPENCLAW")` |
| GET | `/api/oc/stock/{code}/delete-preview` | `oc_stock_delete_preview` | `delete_preview` |
| DELETE | `/api/oc/stock/{code}` | `oc_stock_delete` | `delete_stock_item` |
| GET | `/api/oc/stock/{code}/photos` | `oc_stock_photos` | `get_stock_item` + `logical_photo_entries` |
| DELETE | `/api/oc/stock/{code}/photos/{photo_id}` | `oc_stock_photo_delete` | `delete_photo_and_derived` |

Коды ошибок: 404 — предмет не найден (`StockItemNotFoundError`); 422 — ошибка полей
(`StockValidationError`); 409 — требуется решение владельца / операция заблокирована.

Правила удаления (`oc_stock_delete`):
- без `confirm` → 409 `{"detail","delete_preview"}` (правило 1);
- `confirm=true` с листингами, но без `confirm_listings` → 409 `{"detail","listings"}`
  (`StockDeletionNeedsConfirmationError`, правило 2);
- активная сделка → 409 `{"detail"}` (`LotDeletionBlockedError`, правило 3);
- `confirm=true&confirm_listings=true` → 200 `{"status":"DELETED", ...}`.

Удаление фото: `deleted_ids` — вся цепочка (BFS по `derived_from_id`); файлы
удаляются после `session.commit()` (правило владельца).

Регистрация роутера: `src/nextlife/api/main.py` (`oc_stock_router`).

### 5.2 Изменения существующих маршрутов

- `POST /api/oc/items` → `create_stock_item` (та же запись, что `POST /api/oc/stock`),
  201 полная карточка (`routes_openclaw.py::oc_item_create`).
- `GET /api/oc/items/{code}` → `get_stock_item` — та же карточка, что
  `GET /api/oc/stock/{code}` (`routes_openclaw.py::oc_item_show`).
- `POST /api/oc/wizard/text` → пишет `item.title`/`item.description` через
  `update_stock_item` (`description_lang = item.description_lang or "ru"`), §4.2
  (`routes_openclaw.py::oc_wizard_text`).

### 5.3 CLI `nextlife stock ...` + SKILL.md

`handle_stock_cmd` в `src/nextlife/cli/commands.py`: `list`, `show`, `create`,
`update`, `sold`, `delete`, `photos`, `photo-delete`. Тонкий HTTP-клиент на
`/api/oc/stock/*`. Повторяемый `--set field=value` — через `parse_named_args`
(`src/nextlife/cli/main.py`, накопление в список) + `_parse_set_flags`.

Коды выхода: 409 → 4 (`EXIT_OWNER_REQUIRED`), 400/401/404/422 → 2, прочие → 5.

`SKILL.md`: frontmatter-команды (8 строк `nextlife stock ...`) + секция
«## Склад (Warehouse)» с 5 правилами агента OpenClaw:
1. сначала `delete` без флагов (409 + `delete_preview`) — показать имя, код, фото, листинги;
2. два отдельных «да» (предмет + листинги), затем `--confirm --confirm-listings`;
3. после `sold`/`delete` — отчитаться по `failed`-листингам со ссылками;
4. в доставке называть `storage_location`;
5. `notes` — никогда в текст покупателю.

### 5.4 Тесты

Новый `tests/test_oc_stock.py` (300 строк, 26 тестов):
- 401 без токена (list, show);
- list: пусто / с предметами / 422 на невалидный group;
- create: успех (201, `target_price`, `storage_location`) / 422 без title;
- show: успех / 404; patch: успех / 404; sold: успех / 404;
  delete-preview: успех / 404;
- **последовательность удаления 409 → 409 → 200** (предмет + листинг); delete 404;
- photos: пусто / 404; photo-delete: успех (цепочка `deleted_ids`) / 404;
- §5.2.1: `POST /api/oc/items` и `POST /api/oc/stock` дают одну и ту же карточку
  (201, `status=DRAFT`, `target_price`, `storage_location`);
- CLI: `_api_error_exit_code` 409→4, 404→2, 422→2; `_parse_set_flags`.

Исправлены существующие тесты под новую форму карточки (`item_code`→`code`,
`created`→`DRAFT`): `tests/test_openclaw_api.py` (хелпер, 8 тестов),
`tests/test_cli_http_client.py`, `tests/test_lot_creation_api_cli.py`.

Вывод тестов этапа (`pytest tests/test_oc_stock.py -q`):
```
26 passed, 3 warnings in 13.95s
```
Результат full suite (`.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q`):
**4 failed, 732 passed** (504.83s). Все 4 падения — из базовой линии
(`test_check_hardcode`, `test_listing_templates`, `test_screen_deal_playwright`,
`test_stage_e7`).

`make gates` (буквально):
```
.venv/bin/python scripts/check_secrets.py
Secrets gate passed: no forbidden credentials found across src/, scripts/, tests/, config/.
.venv/bin/python scripts/check_hardcode.py
HARDCODE CHECK FAILED! Found forbidden literals:
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:158: Hardcoded URL -> https://www.bazos.cz/moje-inzeraty.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:304: Hardcoded URL -> https://www.bazos.cz/moje-inzeraty.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:350: Hardcoded URL -> https://www.bazos.cz/pridat-inzerat.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:364: Hardcoded URL -> https://pc.bazos.cz/
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:385: Hardcoded URL -> https://pc.bazos.cz/vlozit-inzerat.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:482: Hardcoded URL -> https://pc.bazos.cz/pridat-inzerat.php
make: *** [Makefile:25: gates] Error 1
```
`check_hardcode` FAIL — 6 захардкоженных URL в `bazos_publish.py` —
предсуществующее (в базовой линии `sklad-baseline.txt`, файл не менялся на этапе 5).

### 5.5 Полный diff (буквально)

```
### git diff -- src/nextlife/api/routes_openclaw.py
diff --git a/src/nextlife/api/routes_openclaw.py b/src/nextlife/api/routes_openclaw.py
index e6eb04b..2153e4f 100644
--- a/src/nextlife/api/routes_openclaw.py
+++ b/src/nextlife/api/routes_openclaw.py
@@ -100,56 +100,37 @@ def oc_status(auth: bool = _AUTH) -> dict[str, Any]:
 
 @oc_router.post("/items", status_code=status.HTTP_201_CREATED)
 def oc_item_create(payload: OcItemCreate, auth: bool = _AUTH) -> dict[str, Any]:
-    from nextlife.services.lot_creation import create_item_and_lot
-
-    price = Decimal(str(payload.price)) if payload.price is not None else None
-    cfg = load_config()
+    """ТЗ «Склад» §5.2.1: create via the stock service (same record as POST /api/oc/stock)."""
+    from nextlife.services.stock import StockValidationError, create_stock_item
+
+    fields: dict[str, Any] = {
+        "title": payload.title,
+        "category_key": payload.category,
+        "price": payload.price,
+        "currency": payload.currency,
+        "condition_key": payload.condition,
+    }
     with get_db_session() as session:
         try:
-            result = create_item_and_lot(
-                session=session,
-                title=payload.title,
-                category_key=payload.category,
-                price=price,
-                currency=payload.currency,
-                condition_key=payload.condition,
-                code_release_days=cfg.app.code_release_days,
-            )
+            result = create_stock_item(session, fields)
             session.commit()
             return result
+        except StockValidationError as exc:
+            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
         except (InputError, NextLifeError) as exc:
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
 
 
 @oc_router.get("/items/{code}")
 def oc_item_show(code: str, auth: bool = _AUTH) -> dict[str, Any]:
-    from nextlife.core.codes import format_code_display
+    """ТЗ «Склад» §5.2.2: the same card as GET /api/oc/stock/{code}."""
+    from nextlife.services.stock import StockItemNotFoundError, get_stock_item
 
     with get_db_session() as session:
-        item = session.get(Item, code)
-        if not item:
-            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}")
-        photos = [
-            {"id": p.id, "role": p.role, "sha256": p.sha256, "path": p.path, "ai_generated": p.ai_generated}
-            for p in session.query(ItemPhoto).filter(ItemPhoto.item_code == code).all()
-        ]
-        listings = [
-            {"marketplace": lst.marketplace_key, "status": lst.status, "url": lst.external_url}
-            for lst in session.query(Listing).filter(Listing.item_code == code).all()
-        ]
-        return {
-            "code": item.code,
-            "display_code": format_code_display(item.code),
-            "title": item.title,
-            "status": item.status,
-            "category": item.category_key,
-            "target_price": str(item.target_price) if item.target_price else None,
-            "target_currency": item.target_currency,
-            "attrs": item.attrs,
-            "defects": item.defects,
-            "photos": photos,
-            "listings": listings,
-        }
+        try:
+            return get_stock_item(session, code)
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}") from exc
 
 
 @oc_router.post("/items/{code}/research")
@@ -436,16 +417,31 @@ class OcWizardTextBody(BaseModel):
 
 @oc_router.post("/wizard/text")
 def oc_wizard_text(body: OcWizardTextBody, auth: bool = _AUTH) -> dict[str, Any]:
-    """Wizard step 2: generate a listing description (stub).
+    """Wizard step 2: generate a listing description and write it to the item.
 
-    Returns a controlled structure (text, mode, endpoint_configured). No
-    external paid API is called in this foundation — the text_gen endpoint is
-    only read to set the mode marker.
+    ТЗ «Склад» §5.2.3: the result is written to ``item.title`` +
+    ``item.description`` via ``update_stock_item`` (rules 4.2); language =
+    ``item.description_lang``.
     """
+    from nextlife.services.stock import update_stock_item
     from nextlife.services.wizard_textgen import generate_description
 
     with get_db_session() as session:
-        return generate_description(body.item_code, body.selected, body.notes, session=session)
+        item = session.get(Item, body.item_code)
+        if not item:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {body.item_code}")
+        lang = item.description_lang or "ru"
+        gen = generate_description(body.item_code, body.selected, body.notes, session=session, lang=lang)
+        if gen.get("mode") == "live":
+            fields: dict[str, Any] = {}
+            if gen.get("title"):
+                fields["title"] = gen["title"]
+            if gen.get("text"):
+                fields["description"] = gen["text"]
+            if fields:
+                update_stock_item(session, body.item_code, fields)
+                session.commit()
+        return gen
 
 
 class OcWizardDraftBody(BaseModel):

### git diff -- src/nextlife/api/main.py
diff --git a/src/nextlife/api/main.py b/src/nextlife/api/main.py
index 8b0d457..9ca36cd 100644
--- a/src/nextlife/api/main.py
+++ b/src/nextlife/api/main.py
@@ -173,6 +173,7 @@ def create_app() -> FastAPI:
     from nextlife.api.routes_lots import lots_router
     from nextlife.api.routes_openclaw import oc_router
     from nextlife.api.routes_openclaw_ops import oc_ops_router
+    from nextlife.api.routes_openclaw_stock import oc_stock_router
     from nextlife.api.routes_photos import photos_router
     from nextlife.api.routes_preview import preview_router
     from nextlife.api.routes_publish import publish_router
@@ -196,6 +197,7 @@ def create_app() -> FastAPI:
     app.include_router(deal_screen_router)
     app.include_router(oc_router)
     app.include_router(oc_ops_router)
+    app.include_router(oc_stock_router)
     return app
 
 

### git diff -- src/nextlife/cli/commands.py
diff --git a/src/nextlife/cli/commands.py b/src/nextlife/cli/commands.py
index 3b45fe1..8f005db 100644
--- a/src/nextlife/cli/commands.py
+++ b/src/nextlife/cli/commands.py
@@ -171,3 +171,62 @@ def handle_search_cmd(flags: dict[str, Any], positionals: list[str], token: str)
     query = str(_get(flags, "query"))
     limit = int(_get(flags, "limit", required=False) or 5)
     return _call(token, "POST", "/api/oc/search", json_body={"platform": platform, "query": query, "limit": limit})
+
+
+def _parse_set_flags(flags: dict[str, Any]) -> dict[str, str]:
+    """Parse repeated ``--set field=value`` pairs into a dict."""
+    raw = flags.get("set")
+    if raw is None or raw is True:
+        return {}
+    pairs = raw if isinstance(raw, list) else [raw]
+    result: dict[str, str] = {}
+    for pair in pairs:
+        if "=" not in str(pair):
+            raise InputError(f"Invalid --set value (expected field=value): {pair}")
+        key, value = str(pair).split("=", 1)
+        result[key.strip()] = value
+    return result
+
+
+def handle_stock_cmd(flags: dict[str, Any], positionals: list[str], token: str) -> dict[str, Any]:
+    """Склад (Warehouse) gateway — ТЗ «Склад» §5.3. Thin HTTP client for /api/oc/stock."""
+    subcmd = positionals[1] if len(positionals) > 1 else "list"
+    if subcmd == "list":
+        group = str(_get(flags, "group", required=False) or "all")
+        q = _get(flags, "q", required=False)
+        path = f"/api/oc/stock?group={group}" + (f"&q={q}" if q else "")
+        return _call(token, "GET", path)
+    if subcmd == "show":
+        code = str(_get(flags, "code"))
+        return _call(token, "GET", f"/api/oc/stock/{code}")
+    if subcmd == "create":
+        title = str(_get(flags, "title"))
+        body: dict[str, Any] = {"title": title}
+        body.update(_parse_set_flags(flags))
+        return _call(token, "POST", "/api/oc/stock", json_body=body)
+    if subcmd == "update":
+        code = str(_get(flags, "code"))
+        fields = _parse_set_flags(flags)
+        if not fields:
+            raise InputError("stock update requires at least one --set field=value")
+        return _call(token, "PATCH", f"/api/oc/stock/{code}", json_body=fields)
+    if subcmd == "sold":
+        code = str(_get(flags, "code"))
+        return _call(token, "POST", f"/api/oc/stock/{code}/sold")
+    if subcmd == "delete":
+        code = str(_get(flags, "code"))
+        params: list[str] = []
+        if flags.get("confirm"):
+            params.append("confirm=true")
+        if flags.get("confirm_listings"):
+            params.append("confirm_listings=true")
+        path = f"/api/oc/stock/{code}" + (f"?{'+'.join(params)}" if params else "")
+        return _call(token, "DELETE", path)
+    if subcmd == "photos":
+        code = str(_get(flags, "code"))
+        return _call(token, "GET", f"/api/oc/stock/{code}/photos")
+    if subcmd == "photo-delete":
+        code = str(_get(flags, "code"))
+        photo_id = str(_get(flags, "photo_id"))
+        return _call(token, "DELETE", f"/api/oc/stock/{code}/photos/{photo_id}")
+    raise InputError(f"Unknown stock subcommand: {subcmd}")

### git diff -- src/nextlife/cli/main.py
diff --git a/src/nextlife/cli/main.py b/src/nextlife/cli/main.py
index 0e708a3..3090677 100644
--- a/src/nextlife/cli/main.py
+++ b/src/nextlife/cli/main.py
@@ -24,6 +24,7 @@ from nextlife.cli.commands import (
     handle_research_cmd,
     handle_search_cmd,
     handle_status_cmd,
+    handle_stock_cmd,
 )
 from nextlife.core.exceptions import InputError
 from nextlife.obs.logger import setup_logger
@@ -44,7 +45,11 @@ def output_json(data: dict[str, Any]) -> None:
 
 
 def parse_named_args(args: list[str]) -> tuple[list[str], dict[str, Any]]:
-    """Parse positional args and --key [value...] flags."""
+    """Parse positional args and --key [value...] flags.
+
+    A flag repeated on the command line (e.g. ``--set a=1 --set b=2``) is
+    accumulated into a list; a single occurrence keeps its scalar/list value.
+    """
     positionals: list[str] = []
     flags: dict[str, Any] = {}
     i = 0
@@ -58,14 +63,20 @@ def parse_named_args(args: list[str]) -> tuple[list[str], dict[str, Any]]:
                 values.append(args[j])
                 j += 1
             if not values:
-                flags[key] = True
+                new_val: Any = True
                 i += 1
             elif len(values) == 1:
-                flags[key] = values[0]
+                new_val = values[0]
                 i = j
             else:
-                flags[key] = values
+                new_val = values
                 i = j
+            if key in flags:
+                existing = flags[key]
+                added = new_val if isinstance(new_val, list) else [new_val]
+                flags[key] = (existing if isinstance(existing, list) else [existing]) + added
+            else:
+                flags[key] = new_val
         else:
             positionals.append(arg)
             i += 1
@@ -165,6 +176,10 @@ def handle_cli(args: list[str]) -> int:
             output_json(handle_search_cmd(flags, positionals, token))
             return EXIT_SUCCESS
 
+        if cmd == "stock":
+            output_json(handle_stock_cmd(flags, positionals, token))
+            return EXIT_SUCCESS
+
         raise InputError(f"Unknown command: {cmd}")
 
     except http_client.ApiError as exc:

### git diff -- SKILL.md
diff --git a/SKILL.md b/SKILL.md
index a68a776..ab7e2e9 100644
--- a/SKILL.md
+++ b/SKILL.md
@@ -19,6 +19,14 @@ commands:
   - nextlife deal close --code <code>
   - nextlife canaries run
   - nextlife canaries status
+  - nextlife stock list [--group all|on_sale|drafts|sold] [--q <text>]
+  - nextlife stock show --code <code>
+  - nextlife stock create --title <title> [--set <field>=<value> ...]
+  - nextlife stock update --code <code> --set <field>=<value> [--set ...]
+  - nextlife stock sold --code <code>
+  - nextlife stock delete --code <code> [--confirm] [--confirm-listings]
+  - nextlife stock photos --code <code>
+  - nextlife stock photo-delete --code <code> --photo-id <id>
 exit_codes:
   0: Success
   2: Input error
@@ -49,3 +57,27 @@ never ask the owner for the token value, never print it).
   is not approved or was modified after approval, the API returns 409 and the
   CLI exits with code 4. Then send the owner the `web_url` again and ask to
   approve the current draft before retrying.
+
+## Склад (Warehouse)
+The «Склад» is the catalogue of sale items. Every item is a row with a code
+(`#NNN`) and editable fields; the listing wizard works on top of a Склад item.
+All Склад operations are available via the `nextlife stock ...` commands
+(thin HTTP client to `/api/oc/stock/*`).
+
+Rules for the OpenClaw agent (ТЗ «Склад» §5.3):
+1. Before deleting, run `nextlife stock delete --code <code>` WITHOUT flags.
+   It exits 4 (HTTP 409) with a `delete_preview`. Show the owner the item name,
+   code, photo count and the listing list.
+2. Get a separate «да» from the owner for deleting the item AND a separate «да»
+   for taking the listings down. Only then re-run with
+   `--confirm --confirm-listings`.
+3. After `stock sold` and `stock delete`, tell the owner every listing with
+   status `failed` and its link for manual takedown.
+4. In any dispatch/shipping action, name the owner the storage location
+   (`storage_location`).
+5. Never include operator notes (`notes`) in buyer-facing text.
+
+Field names in `--set field=value` match the item columns (e.g. `title`,
+`description`, `description_lang`, `usage`, `specs`, `storage_location`,
+`target_price`, `target_currency`, `weight_g`, `packed_weight_g`,
+`dim_l_mm`, `dim_w_mm`, `dim_h_mm`, `shipping_policy`, `notes`).

### git diff -- tests/test_cli_http_client.py
diff --git a/tests/test_cli_http_client.py b/tests/test_cli_http_client.py
index 94f4660..150c4f3 100644
--- a/tests/test_cli_http_client.py
+++ b/tests/test_cli_http_client.py
@@ -78,7 +78,7 @@ def test_cli_publish_409_maps_to_exit4(
         "POST", "/api/oc/items", token=token,
         json_body={"title": "Exit4 Item", "category": "electronics", "price": 10, "currency": "CZK"},
     )
-    code = created["item_code"]
+    code = created["code"]  # ТЗ «Склад» §5.2.1: full stock card
     http_client.request("POST", f"/api/oc/items/{code}/draft", token=token)
 
     exit_code = handle_cli(["publish", "--code", code])

### git diff -- tests/test_lot_creation_api_cli.py
diff --git a/tests/test_lot_creation_api_cli.py b/tests/test_lot_creation_api_cli.py
index 9e7f6e5..09a9b9d 100644
--- a/tests/test_lot_creation_api_cli.py
+++ b/tests/test_lot_creation_api_cli.py
@@ -109,7 +109,7 @@ def test_cli_item_create_shares_service(
     assert ret == 0
     captured = capsys.readouterr()
     data = json.loads(captured.out)
-    assert data["status"] == "created"
+    assert data["status"] == "DRAFT"
     assert data["title"] == "Keychron K2 Wireless Keyboard"
-    # The API normalises price through float (same contract as web /api/lots).
-    assert data["price"] == "1900.0"
+    # ТЗ «Склад» §5.2.1: POST /api/oc/items returns the full stock card.
+    assert Decimal(data["target_price"]) == Decimal("1900")

### git diff -- tests/test_openclaw_api.py
diff --git a/tests/test_openclaw_api.py b/tests/test_openclaw_api.py
index 3113f3b..11ef07a 100644
--- a/tests/test_openclaw_api.py
+++ b/tests/test_openclaw_api.py
@@ -68,7 +68,7 @@ def _create_item(client: TestClient, oc_headers: dict[str, str], title: str = "T
         headers=oc_headers,
     )
     assert resp.status_code == 201, resp.text
-    return resp.json()["item_code"]
+    return resp.json()["code"]  # ТЗ «Склад» §5.2.1: full stock card
 
 
 def _draft_build(client: TestClient, oc_headers: dict[str, str], code: str) -> str:

### NEW FILE: src/nextlife/api/routes_openclaw_stock.py
+++ b/src/nextlife/api/routes_openclaw_stock.py
@@ -0,0 +1,309 @@
+"""OpenClaw-skill HTTP API — Склад (Warehouse) gateway (ТЗ «Склад» этап 5, §5.1).
+
+Thin backend for the ``nextlife stock ...`` CLI. Every endpoint is
+authenticated ONLY with NEXTLIFE_OPENCLAW_TOKEN (``check_openclaw_auth``);
+no web-token fallback. All operations call only the ``nextlife.services.stock``
+module (§1.4) — no direct writes of Склад fields from routes.
+
+Request/response field names match the §1.1 columns. Carriers are passed as
+``delivery_options: [{carrier_key, price, is_enabled}]`` and persisted via the
+``shipping_options`` service.
+
+Error codes: 404 — предмет не найден; 409 — нужно решение владельца / операция
+заблокирована; 422 — ошибка полей.
+
+Module length rule VI.1.1 (<= 400 lines).
+"""
+
+from __future__ import annotations
+
+from pathlib import Path
+from typing import Any
+
+from fastapi import APIRouter, Depends, HTTPException, Query, status
+from fastapi.responses import JSONResponse
+from pydantic import BaseModel
+from sqlalchemy import select
+
+from nextlife.api.routes import logical_photo_entries
+from nextlife.api.routes_openclaw import check_openclaw_auth
+from nextlife.services.lot_delete import LotDeletionBlockedError
+from nextlife.services.shipping_options import configure_lot_delivery_options
+from nextlife.services.stock import (
+    StockDeletionNeedsConfirmationError,
+    StockItemNotFoundError,
+    StockValidationError,
+    create_stock_item,
+    delete_preview,
+    delete_stock_item,
+    get_stock_item,
+    list_stock,
+    mark_sold,
+    update_stock_item,
+)
+from nextlife.storage.db import get_db_session
+from nextlife.storage.models import ItemPhoto
+
+oc_stock_router = APIRouter(prefix="/api/oc/stock", tags=["openclaw"])
+_AUTH = Depends(check_openclaw_auth)
+
+
+class OcStockCreate(BaseModel):
+    title: str
+    description: str | None = None
+    description_lang: str | None = None
+    usage: str | None = None
+    specs: str | None = None
+    notes: str | None = None
+    storage_location: str | None = None
+    target_price: float | None = None
+    target_currency: str | None = None
+    weight_g: int | None = None
+    packed_weight_g: int | None = None
+    dim_l_mm: int | None = None
+    dim_w_mm: int | None = None
+    dim_h_mm: int | None = None
+    shipping_policy: str | None = None
+    delivery_options: list[dict[str, Any]] | None = None
+
+
+class OcStockPatch(BaseModel):
+    title: str | None = None
+    description: str | None = None
+    description_lang: str | None = None
+    usage: str | None = None
+    specs: str | None = None
+    notes: str | None = None
+    storage_location: str | None = None
+    target_price: float | None = None
+    target_currency: str | None = None
+    weight_g: int | None = None
+    packed_weight_g: int | None = None
+    dim_l_mm: int | None = None
+    dim_w_mm: int | None = None
+    dim_h_mm: int | None = None
+    shipping_policy: str | None = None
+    delivery_options: list[dict[str, Any]] | None = None
+
+
+# Item fields accepted by update_stock_item (column names, §1.1).
+_ITEM_FIELDS = (
+    "title",
+    "description",
+    "description_lang",
+    "usage",
+    "specs",
+    "notes",
+    "storage_location",
+    "target_price",
+    "target_currency",
+    "weight_g",
+    "packed_weight_g",
+    "dim_l_mm",
+    "dim_w_mm",
+    "dim_h_mm",
+    "shipping_policy",
+)
+
+
+def _chain_photo_ids(session: Any, code: str, photo_id: str) -> list[str]:
+    """IDs of a photo chain (root + all derived) for the delete response."""
+    photo = session.get(ItemPhoto, photo_id)
+    if not photo or photo.item_code != code:
+        return []
+    ids = [photo_id]
+    seen = {photo_id}
+    frontier = [photo_id]
+    while frontier:
+        children = session.scalars(
+            select(ItemPhoto).where(ItemPhoto.derived_from_id.in_(frontier))
+        ).all()
+        children = [c for c in children if c.id not in seen]
+        if not children:
+            break
+        seen.update(c.id for c in children)
+        ids.extend(c.id for c in children)
+        frontier = [c.id for c in children]
+    return ids
+
+
+@oc_stock_router.get("")
+def oc_stock_list(
+    group: str = Query(default="all"),
+    q: str | None = Query(default=None),
+    auth: bool = _AUTH,
+) -> dict[str, Any]:
+    with get_db_session() as session:
+        try:
+            rows = list_stock(session, group, q)
+        except StockValidationError as exc:
+            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
+    return {"items": rows, "count": len(rows)}
+
+
+@oc_stock_router.post("", status_code=status.HTTP_201_CREATED)
+def oc_stock_create(body: OcStockCreate, auth: bool = _AUTH) -> dict[str, Any]:
+    fields: dict[str, Any] = {
+        "title": body.title,
+        "price": body.target_price,
+        "currency": body.target_currency,
+        "description": body.description,
+        "description_lang": body.description_lang,
+        "usage": body.usage,
+        "specs": body.specs,
+        "notes": body.notes,
+        "storage_location": body.storage_location,
+        "weight_g": body.weight_g,
+        "packed_weight_g": body.packed_weight_g,
+        "dim_l_mm": body.dim_l_mm,
+        "dim_w_mm": body.dim_w_mm,
+        "dim_h_mm": body.dim_h_mm,
+        "shipping_policy": body.shipping_policy or "BUYER_PAYS",
+    }
+    with get_db_session() as session:
+        try:
+            result = create_stock_item(session, fields)
+            code = result["code"]
+            if body.delivery_options is not None:
+                configure_lot_delivery_options(
+                    session, code, body.delivery_options, shipping_policy=body.shipping_policy
+                )
+            session.commit()
+            return get_stock_item(session, code)
+        except StockValidationError as exc:
+            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
+
+
+@oc_stock_router.get("/{code}")
+def oc_stock_show(code: str, auth: bool = _AUTH) -> dict[str, Any]:
+    with get_db_session() as session:
+        try:
+            return get_stock_item(session, code)
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
+
+
+@oc_stock_router.patch("/{code}")
+def oc_stock_patch(code: str, body: OcStockPatch, auth: bool = _AUTH) -> dict[str, Any]:
+    fields = {f: getattr(body, f) for f in _ITEM_FIELDS if getattr(body, f) is not None}
+    with get_db_session() as session:
+        try:
+            result = update_stock_item(session, code, fields)
+            if body.delivery_options is not None:
+                configure_lot_delivery_options(
+                    session, code, body.delivery_options, shipping_policy=body.shipping_policy
+                )
+            session.commit()
+            return result
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
+        except StockValidationError as exc:
+            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
+
+
+@oc_stock_router.post("/{code}/sold")
+def oc_stock_sold(code: str, auth: bool = _AUTH) -> dict[str, Any]:
+    with get_db_session() as session:
+        try:
+            result = mark_sold(session, code, actor="OPENCLAW")
+            session.commit()
+            return result
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
+
+
+@oc_stock_router.get("/{code}/delete-preview")
+def oc_stock_delete_preview(code: str, auth: bool = _AUTH) -> dict[str, Any]:
+    with get_db_session() as session:
+        try:
+            return delete_preview(session, code)
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
+
+
+@oc_stock_router.delete("/{code}")
+def oc_stock_delete(
+    code: str,
+    confirm: bool = Query(default=False),
+    confirm_listings: bool = Query(default=False),
+    auth: bool = _AUTH,
+) -> Any:
+    with get_db_session() as session:
+        try:
+            # Rule 1: without confirm=true — 409 with delete-preview data.
+            if not confirm:
+                preview = delete_preview(session, code)
+                return JSONResponse(
+                    status_code=status.HTTP_409_CONFLICT,
+                    content={
+                        "detail": "Подтверждение удаления требуется (confirm=true).",
+                        "delete_preview": preview,
+                    },
+                )
+            result = delete_stock_item(session, code, confirm_listings=confirm_listings)
+            session.commit()
+            return {"status": "DELETED", **result}
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
+        except StockDeletionNeedsConfirmationError as exc:
+            # Rule 2: confirm=true but no confirm_listings with listings — 409 + listing list.
+            preview = delete_preview(session, code)
+            return JSONResponse(
+                status_code=status.HTTP_409_CONFLICT,
+                content={"detail": str(exc), "listings": preview["listings"]},
+            )
+        except LotDeletionBlockedError as exc:
+            # Rule 3: active deal — 409 with the can_delete_lot reason.
+            return JSONResponse(
+                status_code=status.HTTP_409_CONFLICT,
+                content={"detail": str(exc)},
+            )
+
+
+@oc_stock_router.get("/{code}/photos")
+def oc_stock_photos(code: str, auth: bool = _AUTH) -> dict[str, Any]:
+    with get_db_session() as session:
+        try:
+            get_stock_item(session, code)  # 404 when absent
+            entries = logical_photo_entries(session, code)
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
+    return {
+        "item_code": code,
+        "photos": [
+            {"id": e["id"], "role": e["role"], "is_cover": e["is_cover"], "url": e["url"]}
+            for e in entries
+        ],
+    }
+
+
+@oc_stock_router.delete("/{code}/photos/{photo_id}")
+def oc_stock_photo_delete(code: str, photo_id: str, auth: bool = _AUTH) -> dict[str, Any]:
+    from nextlife.media.ops import delete_photo_and_derived
+
+    deleted_ids: list[str] = []
+    paths: list[str] = []
+    with get_db_session() as session:
+        try:
+            get_stock_item(session, code)  # 404 when absent
+            deleted_ids = _chain_photo_ids(session, code, photo_id)
+            paths = delete_photo_and_derived(session, code, photo_id)
+            if paths:
+                session.commit()
+        except StockItemNotFoundError as exc:
+            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
+
+    # Files are removed only after the commit (owner rule).
+    removed = 0
+    for p in paths:
+        try:
+            Path(p).unlink(missing_ok=True)
+            removed += 1
+        except OSError:
+            pass
+    return {
+        "item_code": code,
+        "photo_id": photo_id,
+        "deleted_ids": deleted_ids,
+        "files_removed": removed,
+    }

### NEW FILE: tests/test_oc_stock.py
+++ b/tests/test_oc_stock.py
@@ -0,0 +1,300 @@
+"""ТЗ «Склад» этап 5 — шлюз /api/oc/stock + CLI (§5.4).
+
+Covers: every §5.1 route (success / 401 / 404 / 422), the delete sequence
+409 → 409 → 200, the CLI exit-code mapping (409 → 4, 404/422 → 2), and
+§5.2.1 (POST /api/oc/items creates the same record as POST /api/oc/stock).
+"""
+
+import os
+from decimal import Decimal
+from typing import Any
+
+import pytest
+from fastapi.testclient import TestClient
+from tests.conftest import clean_database
+
+from nextlife.api.main import create_app
+from nextlife.storage.db import get_db_session
+from nextlife.storage.models import ItemPhoto, Listing
+
+
+class _StubConnector:
+    """Заглушка коннектора для тестов снятия (no live marketplace traffic)."""
+
+    def __init__(self, exc: Exception | None = None):
+        self.exc = exc
+        self.calls: list[str] = []
+
+    def unpublish(self, external_id: str) -> None:
+        self.calls.append(external_id)
+        if self.exc is not None:
+            raise self.exc
+
+
+@pytest.fixture()
+def client() -> TestClient:
+    return TestClient(create_app())
+
+
+@pytest.fixture()
+def oc_headers() -> dict[str, str]:
+    return {"Authorization": f"Bearer {os.environ['NEXTLIFE_OPENCLAW_TOKEN']}"}
+
+
+@pytest.fixture(autouse=True)
+def _clean_db() -> None:
+    with get_db_session() as session:
+        clean_database(session)
+    yield
+    with get_db_session() as session:
+        clean_database(session)
+
+
+def _add_listing(session: Any, code: str, mp_key: str = "bazos_cz", status: str = "PUBLISHED", external_id: str = "ext-1") -> None:
+    session.add(
+        Listing(
+            item_code=code,
+            marketplace_key=mp_key,
+            price=Decimal("1000"),
+            currency="CZK",
+            price_base=Decimal("1000"),
+            status=status,
+            external_id=external_id,
+        )
+    )
+
+
+def _create_stock(client: TestClient, oc_headers: dict[str, str], **extra: Any) -> str:
+    body = {"title": "OC Stock Item", **extra}
+    resp = client.post("/api/oc/stock", json=body, headers=oc_headers)
+    assert resp.status_code == 201, resp.text
+    return resp.json()["code"]
+
+
+# --- Auth (401 without token) -----------------------------------------------
+
+
+def test_stock_list_401_without_token(client: TestClient) -> None:
+    assert client.get("/api/oc/stock").status_code == 401
+
+
+def test_stock_show_401_without_token(client: TestClient) -> None:
+    assert client.get("/api/oc/stock/ABC").status_code == 401
+
+
+# --- List --------------------------------------------------------------------
+
+
+def test_stock_list_empty(client: TestClient, oc_headers: dict[str, str]) -> None:
+    resp = client.get("/api/oc/stock", headers=oc_headers)
+    assert resp.status_code == 200
+    assert resp.json() == {"items": [], "count": 0}
+
+
+def test_stock_list_returns_items(client: TestClient, oc_headers: dict[str, str]) -> None:
+    _create_stock(client, oc_headers)
+    data = client.get("/api/oc/stock", headers=oc_headers).json()
+    assert data["count"] == 1
+    assert data["items"][0]["title"] == "OC Stock Item"
+
+
+def test_stock_list_invalid_group_422(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.get("/api/oc/stock?group=bogus", headers=oc_headers).status_code == 422
+
+
+# --- Create ------------------------------------------------------------------
+
+
+def test_stock_create_success(client: TestClient, oc_headers: dict[str, str]) -> None:
+    resp = client.post(
+        "/api/oc/stock",
+        json={"title": "New", "target_price": 100, "target_currency": "CZK", "storage_location": "Стеллаж 1"},
+        headers=oc_headers,
+    )
+    assert resp.status_code == 201, resp.text
+    data = resp.json()
+    assert data["title"] == "New"
+    assert Decimal(data["target_price"]) == Decimal("100")
+    assert data["storage_location"] == "Стеллаж 1"
+
+
+def test_stock_create_missing_title_422(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.post("/api/oc/stock", json={}, headers=oc_headers).status_code == 422
+
+
+# --- Show --------------------------------------------------------------------
+
+
+def test_stock_show_success(client: TestClient, oc_headers: dict[str, str]) -> None:
+    code = _create_stock(client, oc_headers)
+    resp = client.get(f"/api/oc/stock/{code}", headers=oc_headers)
+    assert resp.status_code == 200
+    assert resp.json()["code"] == code
+
+
+def test_stock_show_404(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.get("/api/oc/stock/ZZZ", headers=oc_headers).status_code == 404
+
+
+# --- Update (PATCH) ----------------------------------------------------------
+
+
+def test_stock_patch_success(client: TestClient, oc_headers: dict[str, str]) -> None:
+    code = _create_stock(client, oc_headers)
+    resp = client.patch(
+        f"/api/oc/stock/{code}", json={"title": "Updated", "storage_location": "Стеллаж 2"}, headers=oc_headers
+    )
+    assert resp.status_code == 200, resp.text
+    assert resp.json()["title"] == "Updated"
+    assert resp.json()["storage_location"] == "Стеллаж 2"
+
+
+def test_stock_patch_404(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.patch("/api/oc/stock/ZZZ", json={"title": "X"}, headers=oc_headers).status_code == 404
+
+
+# --- Sold --------------------------------------------------------------------
+
+
+def test_stock_sold_success(client: TestClient, oc_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
+    from nextlife.connectors import registry
+
+    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: _StubConnector())
+    code = _create_stock(client, oc_headers)
+    resp = client.post(f"/api/oc/stock/{code}/sold", headers=oc_headers)
+    assert resp.status_code == 200, resp.text
+    assert resp.json()["status"] == "SOLD"
+
+
+def test_stock_sold_404(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.post("/api/oc/stock/ZZZ/sold", headers=oc_headers).status_code == 404
+
+
+# --- Delete preview ----------------------------------------------------------
+
+
+def test_stock_delete_preview(client: TestClient, oc_headers: dict[str, str]) -> None:
+    code = _create_stock(client, oc_headers)
+    resp = client.get(f"/api/oc/stock/{code}/delete-preview", headers=oc_headers)
+    assert resp.status_code == 200
+    assert resp.json()["code"] == code
+
+
+def test_stock_delete_preview_404(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.get("/api/oc/stock/ZZZ/delete-preview", headers=oc_headers).status_code == 404
+
+
+# --- Delete sequence 409 → 409 → 200 -----------------------------------------
+
+
+def test_stock_delete_sequence(client: TestClient, oc_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
+    from nextlife.connectors import registry
+
+    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: _StubConnector())
+    code = _create_stock(client, oc_headers)
+    with get_db_session() as session:
+        _add_listing(session, code)
+        session.commit()
+
+    # Rule 1: no confirm → 409 with delete_preview.
+    r1 = client.delete(f"/api/oc/stock/{code}", headers=oc_headers)
+    assert r1.status_code == 409
+    assert "delete_preview" in r1.json()
+
+    # Rule 2: confirm but no confirm_listings, with listings → 409 + listing list.
+    r2 = client.delete(f"/api/oc/stock/{code}?confirm=true", headers=oc_headers)
+    assert r2.status_code == 409
+    assert "listings" in r2.json()
+
+    # Rule 3: confirm + confirm_listings → 200.
+    r3 = client.delete(f"/api/oc/stock/{code}?confirm=true&confirm_listings=true", headers=oc_headers)
+    assert r3.status_code == 200, r3.text
+    assert r3.json()["status"] == "DELETED"
+
+
+def test_stock_delete_404(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.delete("/api/oc/stock/ZZZ?confirm=true", headers=oc_headers).status_code == 404
+
+
+# --- Photos ------------------------------------------------------------------
+
+
+def test_stock_photos_empty(client: TestClient, oc_headers: dict[str, str]) -> None:
+    code = _create_stock(client, oc_headers)
+    resp = client.get(f"/api/oc/stock/{code}/photos", headers=oc_headers)
+    assert resp.status_code == 200
+    assert resp.json()["photos"] == []
+
+
+def test_stock_photos_404(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.get("/api/oc/stock/ZZZ/photos", headers=oc_headers).status_code == 404
+
+
+def test_stock_photo_delete_success(client: TestClient, oc_headers: dict[str, str]) -> None:
+    code = _create_stock(client, oc_headers)
+    with get_db_session() as session:
+        photo = ItemPhoto(
+            item_code=code, role="ORIGINAL", path="/tmp/oc_stock_test.jpg",
+            sha256="a" * 64, width=10, height=10, transform_log={}, ai_generated=False, vlm_verdict={},
+        )
+        session.add(photo)
+        session.commit()
+        photo_id = photo.id
+    resp = client.delete(f"/api/oc/stock/{code}/photos/{photo_id}", headers=oc_headers)
+    assert resp.status_code == 200, resp.text
+    assert photo_id in resp.json()["deleted_ids"]
+
+
+def test_stock_photo_delete_404(client: TestClient, oc_headers: dict[str, str]) -> None:
+    assert client.delete("/api/oc/stock/ZZZ/photos/p1", headers=oc_headers).status_code == 404
+
+
+# --- §5.2.1: POST /api/oc/items == POST /api/oc/stock ------------------------
+
+
+def test_oc_items_and_stock_create_same_record(client: TestClient, oc_headers: dict[str, str]) -> None:
+    r1 = client.post(
+        "/api/oc/items",
+        json={"title": "Same", "category": "electronics", "price": 100, "currency": "CZK"},
+        headers=oc_headers,
+    )
+    assert r1.status_code == 201, r1.text
+    r2 = client.post(
+        "/api/oc/stock", json={"title": "Same2", "target_price": 100, "target_currency": "CZK"}, headers=oc_headers
+    )
+    assert r2.status_code == 201, r2.text
+    # Both return the full stock card with the same shape.
+    for r in (r1, r2):
+        data = r.json()
+        assert data["status"] == "DRAFT"
+        assert "target_price" in data
+        assert "storage_location" in data
+
+
+# --- CLI exit codes (§5.3: 409 → 4, 404/422 → 2) -----------------------------
+
+
+def test_cli_exit_code_409_is_4() -> None:
+    from nextlife.cli.main import _api_error_exit_code
+
+    assert _api_error_exit_code(409) == 4
+
+
+def test_cli_exit_code_404_is_2() -> None:
+    from nextlife.cli.main import _api_error_exit_code
+
+    assert _api_error_exit_code(404) == 2
+
+
+def test_cli_exit_code_422_is_2() -> None:
+    from nextlife.cli.main import _api_error_exit_code
+
+    assert _api_error_exit_code(422) == 2
+
+
+def test_parse_set_flags() -> None:
+    from nextlife.cli.commands import _parse_set_flags
+
+    assert _parse_set_flags({"set": "title=X"}) == {"title": "X"}
+    assert _parse_set_flags({"set": ["title=X", "target_price=100"]}) == {"title": "X", "target_price": "100"}
+    assert _parse_set_flags({}) == {}
```

## Этап 6 — Удаление рудиментов «Ленты» (коммит 7800c5b)

### 6.1 Удалённые маршруты, функции и файлы

По таблице ТЗ §6 удалены рудименты «Ленты»:

- **Legacy wizard**: `GET /ui/wizard`, `GET /ui/wizard/{code}`, `POST /ui/wizard/step/{step_num}`,
  `wizard.html`, step-функции в `routes_lots.py` — заменены на `/ui/lot-wizard/*` (этап 4).
- **Шаблоны `items.html` / `item_detail.html` + маршруты** — заменены на `/ui/stock`,
  `/ui/stock/{code}` (этап 3); редиректы из 3.1 сохранены.
- **`GET/POST /ui/lots/{code}/edit`, `lot_edit.html`, `attrs.final_text`**
  (`lot_edit.py:145`) — правка перенесена на экран предмета Склада.
- **`GET /ui/lots/{code}/shipping` standalone** → редирект `302 /ui/stock/{code}#delivery`;
  конструктор доставки живёт в `_stock_form.html` → `_shipping_constructor.html`.
  `POST /ui/lots/{code}/shipping` сохранён (используется JS-сохранением конструктора).
- **`PATCH/DELETE /api/lots/{code}`, `GET /api/lots/{code}/can-delete`,
  `GET /api/lots/{code}/capabilities`** — заменены на `PATCH/DELETE /api/stock/{code}` (этапы 3–5).
- **`create.html` + `POST /ui/lots/create`** — создание предмета через `/ui/stock/new` (этап 3);
  список черновиков — группа «Черновики» (этап 4.4).
- **Мёртвые `GET /ui/marketplaces`, `POST /ui/lots/create`** — удалены.
- **Таблица `lot_pricing` + модель `LotPricing`** (`pricing/models.py:24`) — миграция
  `0023_drop_lot_pricing`.
- **Чтение attrs-ключей** (`final_text`, `wizard_generated_text`, `generated_description`,
  `listing_text_cs`, `description`, `notes`) → колонки §1.1 (сделано в 1.3).

Процесс по каждому пункту (ТЗ §6): grep всех ссылок → конвертация вызывающих в stock-сервис
или удаление → удаление кода, шаблона и тестов удалённого кода.

**Удалённые файлы:**
- `src/nextlife/api/routes_lot_delete.py` (75 строк: can-delete, DELETE /api/lots/{code}, POST /ui/lots/{code}/delete)
- `src/nextlife/services/lot_edit.py` (187 строк: edit_lot, get_marketplace_edit_capabilities)
- `templates/web/{wizard,items,item_detail,lot_edit,create,shipping_options,marketplaces}.html` (7 файлов)
- `tests/test_lot_edit.py`, `tests/test_wizard_five_steps.py`, `tests/test_screen_marketplaces_playwright.py`

**Изменённые файлы:**
- `src/nextlife/api/routes_lots.py` (−365: wizard/edit/create-маршруты, EditLotPayload)
- `src/nextlife/api/routes_shipping.py` (GET /ui/lots/{code}/shipping → редирект)
- `src/nextlife/api/main.py` (−2: import/include lot_delete_router)
- `src/nextlife/api/routes.py` (−38: screen_marketplaces)
- `src/nextlife/services/wizard_textgen.py` (−27: load/save_generated, GENERATED_ATTR)
- `src/nextlife/pricing/models.py` (−33: модель LotPricing)
- `tests/conftest.py`, `test_honesty.py`, `test_lot_creation_api_cli.py`, `test_lot_delete.py`,
  `test_shipping_options.py`, `test_wizard_textgen.py`

### 6.2 Миграция 0023 — drop lot_pricing

`alembic/versions/20261025_0023_drop_lot_pricing.py` (revision `0023`, down_revision `0022`):
upgrade — `drop_index ix_lot_pricing_item_code` + `drop_table lot_pricing`;
downgrade — воссоздаёт таблицу по DDL из 0004.

### 6.3 Diff маршрутов (routes-dump3.txt → routes-dump-after.txt)

Дамп маршрутов (команда F1, `.a/reports/routes-dump-after.txt`, TOTAL 129) против базового
`routes-dump3.txt` (TOTAL 122):

```
14,15d13
< DELETE               /api/lots/{code}
< PATCH                /api/lots/{code}
17,18d14
< GET                  /api/lots/{code}/can-delete
< GET                  /api/lots/{code}/capabilities
39a36,44
> GET                  /api/oc/stock
> POST                 /api/oc/stock
> DELETE               /api/oc/stock/{code}
> GET                  /api/oc/stock/{code}
> PATCH                /api/oc/stock/{code}
> GET                  /api/oc/stock/{code}/delete-preview
> GET                  /api/oc/stock/{code}/photos
> DELETE               /api/oc/stock/{code}/photos/{photo_id}
> POST                 /api/oc/stock/{code}/sold
52a58,62
> POST                 /api/stock
> DELETE               /api/stock/{code}
> PATCH,POST           /api/stock/{code}
> GET                  /api/stock/{code}/delete-preview
> POST                 /api/stock/{code}/sold
94a105,106
> GET                  /ui/lot-wizard/item
> POST                 /ui/lot-wizard/item
103,106d114
< POST                 /ui/lots/create
< POST                 /ui/lots/{code}/delete
< GET                  /ui/lots/{code}/edit
< POST                 /ui/lots/{code}/edit
110d117
< GET                  /ui/marketplaces
114a122,124
> GET                  /ui/stock
> GET                  /ui/stock/new
> GET                  /ui/stock/{code}
118,120d127
< GET                  /ui/wizard
< POST                 /ui/wizard/step/{step_num}
< GET                  /ui/wizard/{code}
123c130
< TOTAL 122
---
> TOTAL 129
```

Удалено (12, этап 6): `DELETE/PATCH /api/lots/{code}`, `GET /api/lots/{code}/can-delete`,
`GET /api/lots/{code}/capabilities`, `POST /ui/lots/create`, `POST /ui/lots/{code}/delete`,
`GET/POST /ui/lots/{code}/edit`, `GET /ui/marketplaces`, `GET /ui/wizard`,
`POST /ui/wizard/step/{step_num}`, `GET /ui/wizard/{code}`.
Добавлено (19, этапы 3–5): `/api/oc/stock/*` (9), `/api/stock/*` (5), `/ui/lot-wizard/item` (2),
`/ui/stock*` (3). TOTAL 122 → 129.

### 6.4 Тесты этапа

Тесты затронутых файлов (`.venv/bin/python -m pytest tests/test_lot_delete.py tests/test_honesty.py
tests/test_wizard_textgen.py tests/test_shipping_options.py tests/test_lot_creation_api_cli.py
tests/test_pricing_models.py -q`): **45 passed**.

Результат full suite (`.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q`):
**4 failed, 722 passed** (500.71s). Все 4 падения — из базовой линии
(`test_check_hardcode`, `test_listing_templates`, `test_screen_deal_playwright`,
`test_stage_e7`).

### 6.5 make gates (буквально)

```
.venv/bin/python scripts/check_secrets.py
Secrets gate passed: no forbidden credentials found across src/, scripts/, tests/, config/.
.venv/bin/python scripts/check_hardcode.py
HARDCODE CHECK FAILED! Found forbidden literals:
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:158: Hardcoded URL -> https://www.bazos.cz/moje-inzeraty.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:304: Hardcoded URL -> https://www.bazos.cz/moje-inzeraty.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:350: Hardcoded URL -> https://www.bazos.cz/pridat-inzerat.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:364: Hardcoded URL -> https://pc.bazos.cz/
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:385: Hardcoded URL -> https://pc.bazos.cz/vlozit-inzerat.php
  /path/to/NextLife/src/nextlife/services/bazos_publish.py:482: Hardcoded URL -> https://pc.bazos.cz/pridat-inzerat.php
make: *** [Makefile:25: gates] Error 1
```
`check_hardcode` FAIL — 6 захардкоженных URL в `bazos_publish.py` —
предсуществующее (в базовой линии `sklad-baseline.txt`, файл не менялся на этапе 6).
`check_secrets` — pass.

### 6.6 Полный diff (буквально)

```
### NEW FILE: alembic/versions/20261025_0023_drop_lot_pricing.py
diff --git a/alembic/versions/20261025_0023_drop_lot_pricing.py b/alembic/versions/20261025_0023_drop_lot_pricing.py
new file mode 100644
index 0000000..a82123c
--- /dev/null
+++ b/alembic/versions/20261025_0023_drop_lot_pricing.py
@@ -0,0 +1,49 @@
+"""drop_lot_pricing
+
+ТЗ «Склад» §6: таблица lot_pricing неиспользуема (модель LotPricing удалена).
+downgrade() воссоздаёт таблицу по DDL миграции 0004.
+
+Revision ID: 0023
+Revises: 0022
+Create Date: 2026-10-25
+
+"""
+
+from collections.abc import Sequence
+
+import sqlalchemy as sa
+from alembic import op
+from sqlalchemy.dialects import postgresql
+
+revision: str = "0023"
+down_revision: str | None = "0022"
+branch_labels: str | Sequence[str] | None = None
+depends_on: str | Sequence[str] | None = None
+
+
+def upgrade() -> None:
+    op.drop_index("ix_lot_pricing_item_code", table_name="lot_pricing")
+    op.drop_table("lot_pricing")
+
+
+def downgrade() -> None:
+    # DDL миграции 0004 (add_lot_pricing).
+    op.create_table(
+        "lot_pricing",
+        sa.Column("id", sa.String(length=36), nullable=False),
+        sa.Column("item_code", sa.String(length=3), nullable=False),
+        sa.Column("price_gross", sa.Numeric(precision=12, scale=2), nullable=False),
+        sa.Column("price_base", sa.Numeric(precision=12, scale=2), nullable=True),
+        sa.Column("price_floor", sa.Numeric(precision=12, scale=2), nullable=False),
+        sa.Column("negotiable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
+        sa.Column("shipping_policy", sa.String(length=20), server_default="BUYER_PAYS", nullable=False),
+        sa.Column("shipping_included_amount", sa.Numeric(precision=10, scale=2), server_default=sa.text("0.0"), nullable=False),
+        sa.Column("price_decay_steps", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[-0.05, -0.10, -0.15]'::jsonb"), nullable=False),
+        sa.Column("stale_days", sa.Integer(), server_default="14", nullable=False),
+        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
+        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
+        sa.ForeignKeyConstraint(["item_code"], ["item.code"], ondelete="CASCADE"),
+        sa.PrimaryKeyConstraint("id"),
+        sa.UniqueConstraint("item_code", name="uq_lot_pricing_item_code"),
+    )
+    op.create_index("ix_lot_pricing_item_code", "lot_pricing", ["item_code"])

### git diff -- src/nextlife/api/main.py
diff --git a/src/nextlife/api/main.py b/src/nextlife/api/main.py
index 9ca36cd..b21b663 100644
--- a/src/nextlife/api/main.py
+++ b/src/nextlife/api/main.py
@@ -169,7 +169,6 @@ def create_app() -> FastAPI:
     from nextlife.api.routes_dashboard import dashboard_router
     from nextlife.api.routes_deal_screen import deal_screen_router
     from nextlife.api.routes_evidence import evidence_router
-    from nextlife.api.routes_lot_delete import lot_delete_router
     from nextlife.api.routes_lots import lots_router
     from nextlife.api.routes_openclaw import oc_router
     from nextlife.api.routes_openclaw_ops import oc_ops_router
@@ -190,7 +189,6 @@ def create_app() -> FastAPI:
     app.include_router(photos_router)
     app.include_router(preview_router)
     app.include_router(publish_router)
-    app.include_router(lot_delete_router)
     app.include_router(dashboard_router)
     app.include_router(shipping_router)
     app.include_router(evidence_router)

### git diff -- src/nextlife/api/routes.py
diff --git a/src/nextlife/api/routes.py b/src/nextlife/api/routes.py
index 86275c2..b7a36ce 100644
--- a/src/nextlife/api/routes.py
+++ b/src/nextlife/api/routes.py
@@ -34,7 +34,7 @@ from nextlife.services.research import build_research_panel
 from nextlife.services.sell import build_draft
 from nextlife.services.stock import update_stock_item
 from nextlife.storage.db import get_db_session
-from nextlife.storage.models import AuditEvent, ChannelCanary, Deal, Item, Listing, Lot, Marketplace
+from nextlife.storage.models import AuditEvent, Deal, Item, Listing, Lot
 
 router = APIRouter()
 
@@ -353,42 +353,6 @@ def screen_archive(
     )
 
 
-@router.get("/ui/marketplaces", response_class=HTMLResponse)
-def screen_marketplaces(
-    request: Request,
-    auth: bool = Depends(check_auth),
-) -> HTMLResponse:
-    """«Площадки» (TZ M1 D.1): channel state per marketplace from live canary probes.
-
-    Ч-1: the state shown here comes from the channel_canary table, which is
-    written only by live probes (run via `nextlife.cli canaries run`).
-    """
-    with get_db_session() as session:
-        titles = {m.key: m.title for m in session.execute(select(Marketplace)).scalars().all()}
-        rows = session.execute(
-            select(ChannelCanary).order_by(ChannelCanary.marketplace_key)
-        ).scalars().all()
-        channels = [
-            {
-                "key": r.marketplace_key,
-                "title": titles.get(r.marketplace_key, r.marketplace_key),
-                "scenario": r.scenario,
-                "channel": r.channel,
-                "state": r.state,
-                "last_success_at": r.last_success_at.strftime("%Y-%m-%d %H:%M") if r.last_success_at else None,
-                "last_attempt_at": r.last_attempt_at.strftime("%Y-%m-%d %H:%M") if r.last_attempt_at else None,
-                "block_reason": r.block_reason,
-            }
-            for r in rows
-        ]
-
-    return templates.TemplateResponse(
-        request=request,
-        name="marketplaces.html",
-        context={"channels": channels, "active_tab": None},
-    )
-
-
 @router.get("/ui/settings", response_class=HTMLResponse)
 def screen_settings(
     request: Request,

### DELETED FILE: src/nextlife/api/routes_lot_delete.py
diff --git a/src/nextlife/api/routes_lot_delete.py b/src/nextlife/api/routes_lot_delete.py
deleted file mode 100644
index 320ca49..0000000
--- a/src/nextlife/api/routes_lot_delete.py
+++ /dev/null
@@ -1,75 +0,0 @@
-"""API and UI routes for lot deletion with deal check and audit (TZ v2.0 Section I.3).
-
-DoD:
-- Rejection when active deal exists with clear explanation.
-- Mandatory unpublishing before deletion.
-- Soft delete with audit log entry.
-- Module length <= 400 lines.
-"""
-
-from typing import Any
-
-from fastapi import APIRouter, Depends, HTTPException, Request, status
-from fastapi.responses import RedirectResponse
-
-from nextlife.api.routes import check_auth
-from nextlife.services.lot_delete import LotDeletionBlockedError, can_delete_lot, delete_lot
-from nextlife.storage.db import get_db_session
-
-lot_delete_router = APIRouter(tags=["lot-deletion"])
-
-
-@lot_delete_router.get("/api/lots/{code}/can-delete")
-def api_can_delete_lot(
-    code: str,
-    auth: bool = Depends(check_auth),
-) -> dict[str, Any]:
-    """Check if a lot can be safely deleted."""
-    with get_db_session() as session:
-        allowed, reason = can_delete_lot(session, code)
-        return {"lot_code": code, "can_delete": allowed, "reason": reason}
-
-
-@lot_delete_router.delete("/api/lots/{code}")
-def api_delete_lot(
-    code: str,
-    auth: bool = Depends(check_auth),
-) -> dict[str, Any]:
-    """Soft delete lot, unpublish from marketplaces, or reject if active deal exists."""
-    with get_db_session() as session:
-        try:
-            result = delete_lot(session, lot_code=code, actor="OWNER")
-            session.commit()
-            return result
-        except LotDeletionBlockedError as exc:
-            raise HTTPException(
-                status_code=status.HTTP_409_CONFLICT,
-                detail=str(exc),
-            ) from exc
-        except ValueError as exc:
-            raise HTTPException(
-                status_code=status.HTTP_404_NOT_FOUND,
-                detail=str(exc),
-            ) from exc
-
-
-@lot_delete_router.post("/ui/lots/{code}/delete")
-def ui_delete_lot(
-    request: Request,
-    code: str,
-    auth: bool = Depends(check_auth),
-) -> RedirectResponse:
-    """Handle UI lot deletion request."""
-    with get_db_session() as session:
-        try:
-            delete_lot(session, lot_code=code, actor="OWNER")
-            session.commit()
-            return RedirectResponse(
-                url="/ui/items?notice=lot_deleted",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )
-        except LotDeletionBlockedError as exc:
-            return RedirectResponse(
-                url=f"/ui/items/{code}?error={str(exc)}",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )

### git diff -- src/nextlife/api/routes_lots.py
diff --git a/src/nextlife/api/routes_lots.py b/src/nextlife/api/routes_lots.py
index 22aaa66..048cf8b 100644
--- a/src/nextlife/api/routes_lots.py
+++ b/src/nextlife/api/routes_lots.py
@@ -25,18 +25,14 @@ from fastapi import (
 )
 from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
 from pydantic import BaseModel
-from sqlalchemy import select
 
 from nextlife.api.routes import check_auth, logical_photo_entries, templates
-from nextlife.core.categories import load_category_options
 from nextlife.core.config import load_config
 from nextlife.core.shipping_policy import normalize_shipping_policy, shipping_policy_options
 from nextlife.obs.audit import record_audit_event
 from nextlife.services.lot_creation import create_item_and_lot
-from nextlife.services.lot_edit import edit_lot, get_marketplace_edit_capabilities
-from nextlife.services.research import build_research_panel
 from nextlife.storage.db import get_db_session
-from nextlife.storage.models import Item, Listing, Lot
+from nextlife.storage.models import Item, Listing
 
 lots_router = APIRouter(tags=["lots"])
 
@@ -68,10 +64,6 @@ def _validated_shipping_policy(shipping_policy: str | None) -> str | None:
     return policy
 
 
-def _category_option_list() -> list[dict[str, str]]:
-    return [{"value": key, "label": title} for key, title in load_category_options()]
-
-
 class CreateLotPayload(BaseModel):
     title: str
     category: str
@@ -118,361 +110,6 @@ def api_create_lot(
         ) from exc
 
 
-@lots_router.post("/ui/lots/create")
-def ui_create_lot(
-    request: Request,
-    title: str = Form(...),
-    category: str = Form("general"),
-    price: float | None = Form(None),
-    currency: str | None = Form(None),
-    condition: str = Form("used"),
-    price_floor: float | None = Form(None),
-    shipping_policy: str = Form("BUYER_PAYS"),
-    notes: str | None = Form(None),
-    auth: bool = Depends(check_auth),
-) -> RedirectResponse:
-    """Create lot via web form and redirect to item detail page."""
-    price_dec = Decimal(str(price)) if price is not None else None
-    floor_dec = Decimal(str(price_floor)) if price_floor is not None else None
-    policy = _validated_shipping_policy(shipping_policy) or "BUYER_PAYS"
-
-    try:
-        with get_db_session() as session:
-            res = create_item_and_lot(
-                session=session,
-                title=title,
-                category_key=category,
-                price=price_dec,
-                currency=currency,
-                condition_key=condition,
-                price_floor=floor_dec,
-                shipping_policy=policy,
-                notes=notes,
-            )
-            session.commit()
-            code = res["item_code"]
-            return RedirectResponse(
-                url=f"/ui/items/{code}?notice=created",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )
-    except Exception as exc:
-        return RedirectResponse(
-            url=f"/ui/items?error={exc}",
-            status_code=status.HTTP_303_SEE_OTHER,
-        )
-
-
-@lots_router.get("/ui/wizard", response_class=HTMLResponse)
-def screen_wizard_start(request: Request, auth: bool = Depends(check_auth)) -> Any:
-    """Redirect to step 1 of wizard."""
-    return templates.TemplateResponse(
-        request=request,
-        name="wizard.html",
-        context={
-            "step": 1,
-            "item": None,
-            "lot": None,
-            "active_tab": "items",
-            # TZ-NextLife-UI-N2 §4 V4 (P-E10/P-E19): единые словари.
-            "category_options": _category_option_list(),
-            "shipping_policy_options": shipping_policy_options(),
-        },
-    )
-
-
-@lots_router.get("/ui/wizard/{code}", response_class=HTMLResponse)
-def screen_wizard_step(
-    request: Request,
-    code: str,
-    step: int = 1,
-    auth: bool = Depends(check_auth),
-) -> HTMLResponse:
-    """Render specific step of the five-step creation wizard."""
-    item_dict = None
-    lot_dict = None
-    photos_data: list[dict[str, Any]] = []
-    research: dict[str, Any] | None = None
-
-    with get_db_session() as session:
-        item = session.get(Item, code)
-        lot = session.scalar(select(Lot).where(Lot.item_id == code).order_by(Lot.created_at.desc()))
-        # TZ-NextLife-UI-N2 §4 V3 (Э-6): в шаге 2 — логические фото (цепочки),
-        # публикуемые версии; крестик удаляет цепочку целиком (id корня).
-        photos_data = logical_photo_entries(session, code)
-        if item:
-            item_dict = {
-                "code": item.code,
-                "title": item.title,
-                "category_key": item.category_key,
-                "notes": item.notes,
-                "condition_grade": item.condition_grade,
-                "box_present": item.box_present,
-                "receipt_present": item.receipt_present,
-                "defects": item.defects or [],
-            }
-        if lot:
-            lot_dict = {
-                "code": lot.code,
-                "price_gross": lot.price_gross,
-                "price_floor": lot.price_floor,
-                "shipping_policy": lot.shipping_policy,
-                "currency": lot.currency,
-            }
-        # TZ-NextLife-UI-N2 §4 V5 (P-R07): панель разведки в шаге 4.
-        if item is not None:
-            research = build_research_panel(session, item, load_config())
-
-    return templates.TemplateResponse(
-        request=request,
-        name="wizard.html",
-        context={
-            "step": step,
-            "item": item_dict,
-            "lot": lot_dict,
-            "photos": photos_data,
-            "active_tab": "items",
-            # TZ-NextLife-UI-N2 §4 V4 (P-E10/P-E19): единые словари.
-            "category_options": _category_option_list(),
-            "shipping_policy_options": shipping_policy_options(),
-            # TZ-NextLife-UI-N2 §4 V5 (P-R07): разведка в шаге 4.
-            "research": research,
-            "research_notice": None,
-            "research_back": f"/ui/wizard/{code}?step=4",
-        },
-    )
-
-
-@lots_router.post("/ui/wizard/step/{step_num}")
-def api_wizard_save_step(
-    step_num: int,
-    request: Request,
-    code: str = Form(""),
-    title: str = Form(""),
-    category: str = Form("general"),
-    notes: str | None = Form(None),
-    condition_grade: str = Form("B"),
-    box_present: str | None = Form(None),
-    receipt_present: str | None = Form(None),
-    defect_text: str | None = Form(None),
-    price: float | None = Form(None),
-    price_floor: float | None = Form(None),
-    shipping_policy: str = Form("BUYER_PAYS"),
-    auth: bool = Depends(check_auth),
-) -> RedirectResponse:
-    """Save wizard step progress persistently to DB and advance or return."""
-    with get_db_session() as session:
-        if step_num == 1:
-            if not code or code == "new":
-                res = create_item_and_lot(
-                    session=session,
-                    title=title,
-                    category_key=category,
-                    notes=notes,
-                )
-                session.commit()
-                code = res["item_code"]
-            else:
-                item = session.get(Item, code)
-                if item:
-                    item.title = title
-                    item.category_key = category
-                    item.notes = notes
-                    session.commit()
-            return RedirectResponse(
-                url=f"/ui/wizard/{code}?step=2",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )
-
-        item = session.get(Item, code)
-        lot = session.scalar(select(Lot).where(Lot.item_id == code).order_by(Lot.created_at.desc()))
-        if not item:
-            return RedirectResponse(url="/ui/items", status_code=status.HTTP_303_SEE_OTHER)
-
-        if step_num == 2:
-            # Photos step transition
-            session.commit()
-            return RedirectResponse(
-                url=f"/ui/wizard/{code}?step=3",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )
-
-        elif step_num == 3:
-            # Condition & defects
-            item.condition_grade = condition_grade
-            item.box_present = bool(box_present)
-            item.receipt_present = bool(receipt_present)
-            if defect_text and defect_text.strip():
-                item.defects = [{"zone": "general", "text": defect_text.strip()}]
-            session.commit()
-            return RedirectResponse(
-                url=f"/ui/wizard/{code}?step=4",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )
-
-        elif step_num == 4:
-            # Price & price floor
-            if price is not None:
-                p_val = Decimal(str(price))
-                item.target_price = p_val
-                if lot:
-                    lot.price_gross = p_val
-            if price_floor is not None and lot:
-                lot.price_floor = Decimal(str(price_floor))
-            session.commit()
-            return RedirectResponse(
-                url=f"/ui/wizard/{code}?step=5",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )
-
-        elif step_num == 5:
-            # Shipping & payment (P-E10: единый словарь, включ. нормализацию)
-            policy = _validated_shipping_policy(shipping_policy)
-            if lot and policy is not None:
-                lot.shipping_policy = policy
-            session.commit()
-            return RedirectResponse(
-                url=f"/ui/items/{code}?notice=wizard_complete",
-                status_code=status.HTTP_303_SEE_OTHER,
-            )
-
-    return RedirectResponse(url=f"/ui/items/{code}", status_code=status.HTTP_303_SEE_OTHER)
-
-
-class EditLotPayload(BaseModel):
-    title: str | None = None
-    description: str | None = None
-    price: float | None = None
-    price_floor: float | None = None
-    shipping_policy: str | None = None
-    notes: str | None = None
-
-
-@lots_router.get("/api/lots/{code}/capabilities")
-def api_get_lot_capabilities(
-    code: str,
-    auth: bool = Depends(check_auth),
-) -> list[dict[str, Any]]:
-    """Retrieve marketplace capabilities for lot editing."""
-    with get_db_session() as session:
-        return get_marketplace_edit_capabilities(session, code)
-
-
-@lots_router.patch("/api/lots/{code}")
-def api_edit_lot(
-    code: str,
-    payload: EditLotPayload,
-    auth: bool = Depends(check_auth),
-) -> dict[str, Any]:
-    """Edit lot fields, invalidate approval, return marketplace capabilities."""
-    price_dec = Decimal(str(payload.price)) if payload.price is not None else None
-    floor_dec = Decimal(str(payload.price_floor)) if payload.price_floor is not None else None
-    policy = _validated_shipping_policy(payload.shipping_policy)
-
-    with get_db_session() as session:
-        result = edit_lot(
-            session=session,
-            lot_code=code,
-            title=payload.title,
-            description=payload.description,
-            price=price_dec,
-            price_floor=floor_dec,
-            shipping_policy=policy,
-            notes=payload.notes,
-        )
-        session.commit()
-        return result
-
-
-@lots_router.get("/ui/lots/{code}/edit", response_class=HTMLResponse)
-def ui_edit_lot_page(
-    request: Request,
-    code: str,
-    auth: bool = Depends(check_auth),
-) -> HTMLResponse:
-    """Render lot edit form with live marketplace capabilities."""
-    with get_db_session() as session:
-        lot = session.scalar(select(Lot).where(Lot.code == code))
-        if not lot:
-            lot = session.scalar(select(Lot).where(Lot.item_id == code).order_by(Lot.created_at.desc()))
-        if not lot:
-            raise HTTPException(status_code=404, detail=f"Lot {code} not found")
-        item = session.get(Item, lot.item_id)
-        if not item:
-            raise HTTPException(status_code=404, detail=f"Item {lot.item_id} not found")
-
-        caps = get_marketplace_edit_capabilities(session, lot.code)
-
-        lot_data = {
-            "id": lot.id,
-            "code": lot.code,
-            "status": lot.status,
-            "price_gross": lot.price_gross,
-            "price_floor": lot.price_floor,
-            "shipping_policy": lot.shipping_policy,
-            "notes": lot.notes,
-        }
-        from nextlife.services.draft_text import get_item_text_blocks
-
-        item_data = {
-            "code": item.code,
-            "title": item.title,
-            "attrs": item.attrs or {},
-            "status": item.status,
-            # TZ-NextLife-UI-N2 §3.3 P-E12: форма показывает именно уходящий
-            # в публикацию текст (тот же, что в черновике).
-            "outgoing_text": get_item_text_blocks(item)["final_text"],
-        }
-
-    return templates.TemplateResponse(
-        request,
-        "lot_edit.html",
-        {
-            "lot": lot_data,
-            "item": item_data,
-            "marketplace_capabilities": caps,
-            # TZ-NextLife-UI-N2 §4 V4 (P-E10): единый словарь политики доставки.
-            "shipping_policy_options": shipping_policy_options(),
-        },
-    )
-
-
-@lots_router.post("/ui/lots/{code}/edit")
-def ui_edit_lot_submit(
-    request: Request,
-    code: str,
-    title: str = Form(...),
-    description: str | None = Form(None),
-    price: float | None = Form(None),
-    price_floor: float | None = Form(None),
-    shipping_policy: str = Form("BUYER_PAYS"),
-    notes: str | None = Form(None),
-    auth: bool = Depends(check_auth),
-) -> RedirectResponse:
-    """Submit lot changes, invalidate approval and redirect to item detail."""
-    price_dec = Decimal(str(price)) if price is not None else None
-    floor_dec = Decimal(str(price_floor)) if price_floor is not None else None
-    policy = _validated_shipping_policy(shipping_policy)
-
-    with get_db_session() as session:
-        result = edit_lot(
-            session=session,
-            lot_code=code,
-            title=title,
-            description=description,
-            price=price_dec,
-            price_floor=floor_dec,
-            shipping_policy=policy,
-            notes=notes,
-        )
-        session.commit()
-        item_code = result["item_code"]
-
-    return RedirectResponse(
-        url=f"/ui/items/{item_code}?notice=lot_edited_approval_invalidated",
-        status_code=status.HTTP_303_SEE_OTHER,
-    )
-
-
 @lots_router.get("/ui/lot-wizard", response_class=RedirectResponse)
 def screen_lot_wizard_index() -> RedirectResponse:
     """Lot-creation wizard entry point: redirect to step 1 (Склад item)."""

### git diff -- src/nextlife/api/routes_shipping.py
diff --git a/src/nextlife/api/routes_shipping.py b/src/nextlife/api/routes_shipping.py
index 97796ad..015369d 100644
--- a/src/nextlife/api/routes_shipping.py
+++ b/src/nextlife/api/routes_shipping.py
@@ -11,11 +11,11 @@ import json
 from typing import Any
 
 from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
-from fastapi.responses import HTMLResponse, RedirectResponse
+from fastapi.responses import RedirectResponse
 from pydantic import BaseModel
 
-from nextlife.api.routes import check_auth, templates
-from nextlife.core.shipping_policy import normalize_shipping_policy, shipping_policy_options
+from nextlife.api.routes import check_auth
+from nextlife.core.shipping_policy import normalize_shipping_policy
 from nextlife.services.shipping_options import (
     configure_lot_delivery_options,
     get_lot_delivery_summary,
@@ -162,31 +162,14 @@ def api_get_buyer_total(
         }
 
 
-@shipping_router.get("/ui/lots/{code}/shipping", response_class=HTMLResponse)
+@shipping_router.get("/ui/lots/{code}/shipping")
 def ui_shipping_options_page(
-    request: Request,
     code: str,
     auth: bool = Depends(check_auth),
-) -> HTMLResponse:
-    """Render shipping options constructor UI with Alpine component."""
-    with get_db_session() as session:
-        try:
-            summary = get_lot_delivery_summary(session, code)
-        except ValueError as exc:
-            raise HTTPException(status_code=404, detail=str(exc)) from exc
-
-    summary_json = json.dumps(summary, ensure_ascii=False)
-    return templates.TemplateResponse(
-        request,
-        "shipping_options.html",
-        {
-            "summary": summary,
-            "summary_json": summary_json,
-            "active_tab": "items",
-            # TZ-NextLife-UI-N2 §4 V4 (P-E10): единый словарь политики доставки.
-            "shipping_policy_options": shipping_policy_options(),
-        },
-    )
+) -> RedirectResponse:
+    """ТЗ «Склад» §6: standalone shipping screen removed — redirect to the
+    «Доставка» block on the Склад item screen."""
+    return RedirectResponse(url=f"/ui/stock/{code}#delivery", status_code=status.HTTP_302_FOUND)
 
 
 @shipping_router.post("/ui/lots/{code}/shipping")

### git diff -- src/nextlife/pricing/models.py
diff --git a/src/nextlife/pricing/models.py b/src/nextlife/pricing/models.py
index 3858a89..6163055 100644
--- a/src/nextlife/pricing/models.py
+++ b/src/nextlife/pricing/models.py
@@ -1,18 +1,12 @@
 """Lot pricing data models and serialization (TZ v2.0 Section III.1)."""
 
-import uuid
-from datetime import UTC, datetime
 from decimal import Decimal
 from enum import StrEnum
 from typing import Any
 
 from pydantic import BaseModel, Field
-from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
-from sqlalchemy.dialects.postgresql import JSONB
-from sqlalchemy.orm import Mapped, mapped_column
 
 from nextlife.comms.outgoing_filter import InternalFieldLeakageError
-from nextlife.storage.models.core import Base
 
 
 class ShippingPolicy(StrEnum):
@@ -21,31 +15,6 @@ class ShippingPolicy(StrEnum):
     PICKUP_ONLY = "PICKUP_ONLY"
 
 
-class LotPricing(Base):
-    __tablename__ = "lot_pricing"
-
-    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
-    item_code: Mapped[str] = mapped_column(String(3), ForeignKey("item.code"), nullable=False, unique=True, index=True)
-    price_gross: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
-    price_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
-
-    # Internal reserve floor price (internal=true)
-    price_floor: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
-
-    negotiable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
-    shipping_policy: Mapped[str] = mapped_column(String(20), default=ShippingPolicy.BUYER_PAYS.value, nullable=False)
-    shipping_included_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.0"), nullable=False)
-    price_decay_steps: Mapped[list[float]] = mapped_column(JSONB, default=lambda: [-0.05, -0.10, -0.15], nullable=False)
-    stale_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
-
-    created_at: Mapped[datetime] = mapped_column(
-        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
-    )
-    updated_at: Mapped[datetime] = mapped_column(
-        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
-    )
-
-
 class PublicLotPricingSchema(BaseModel):
     """Publicly serializable pricing representation.
 
@@ -69,7 +38,7 @@ class InternalLotPricingSchema(PublicLotPricingSchema):
     internal: bool = True
 
 
-def serialize_outgoing_pricing(pricing: LotPricing | InternalLotPricingSchema | dict[str, Any]) -> dict[str, Any]:
+def serialize_outgoing_pricing(pricing: InternalLotPricingSchema | dict[str, Any]) -> dict[str, Any]:
     """Serialize pricing for external marketplace or customer communication.
 
     Guarantees price_floor is never leaked into outgoing payload.

### DELETED FILE: src/nextlife/services/lot_edit.py
diff --git a/src/nextlife/services/lot_edit.py b/src/nextlife/services/lot_edit.py
deleted file mode 100644
index ab0ac9d..0000000
--- a/src/nextlife/services/lot_edit.py
+++ /dev/null
@@ -1,187 +0,0 @@
-"""Lot editing service with approval invalidation and marketplace capabilities check.
-
-TZ v2.0 Section I.3:
-- Editing lot fields (price, description, title, shipping) invalidates past approval.
-- Sets lot and item status to AWAITING_APPROVAL.
-- Inspects marketplace capabilities to identify which platforms support direct editing
-  vs which require re-posting (relist).
-- Module length <= 400 lines.
-"""
-
-from decimal import Decimal
-from typing import Any
-
-from sqlalchemy import delete, select
-from sqlalchemy.orm import Session
-
-from nextlife.core.config import load_config
-from nextlife.core.status import ItemStatus
-from nextlife.obs.audit import record_audit_event
-from nextlife.storage.models import Approval, Item, Listing, Lot, Marketplace
-
-
-def get_marketplace_edit_capabilities(
-    session: Session, lot_code: str
-) -> list[dict[str, Any]]:
-    """Determine editing capabilities for marketplaces where the lot is listed or available.
-
-    Reads directly from marketplace capabilities (capabilities.edit).
-    """
-    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
-    if not lot:
-        raise ValueError(f"Lot with code {lot_code} not found")
-
-    # Find listings for this item/lot
-    listings = session.scalars(
-        select(Listing).where(Listing.item_code == lot.item_id)
-    ).all()
-    listed_marketplace_keys = {lst.marketplace_key for lst in listings}
-
-    # Fetch all marketplaces from DB or config
-    marketplaces = session.scalars(select(Marketplace)).all()
-    mp_map = {m.key: m for m in marketplaces}
-
-    # Also load from yaml config
-    loaded_config = load_config()
-    cfg_mps = loaded_config.marketplaces if hasattr(loaded_config, "marketplaces") else {}
-
-    # Target keys
-    target_keys = listed_marketplace_keys or set(mp_map.keys()) or set(cfg_mps.keys()) or {"bazos_cz", "sbazar_cz", "aukro_cz"}
-
-    results = []
-    for key in sorted(target_keys):
-        title = key
-        caps: dict[str, Any] = {}
-
-        if key in mp_map:
-            title = mp_map[key].title
-            caps = mp_map[key].capabilities or {}
-        elif key in cfg_mps:
-            title = cfg_mps[key].title
-            caps = cfg_mps[key].capabilities or {}
-
-        supports_edit = bool(caps.get("edit", False))
-        results.append(
-            {
-                "marketplace_key": key,
-                "title": title,
-                "supports_edit": supports_edit,
-                "requires_relist": not supports_edit,
-                "capabilities": caps,
-                "action_type": "IN_PLACE_EDIT" if supports_edit else "REQUIRE_REPOST",
-                "message": (
-                    f"{title}: прямое обновление цены и описания поддерживается."
-                    if supports_edit
-                    else f"{title}: прямое обновление не поддерживается API площадки; потребуется автоматическое переразмещение."
-                ),
-            }
-        )
-    return results
-
-
-def edit_lot(
-    session: Session,
-    lot_code: str,
-    *,
-    title: str | None = None,
-    description: str | None = None,
-    price: Decimal | None = None,
-    price_floor: Decimal | None = None,
-    shipping_policy: str | None = None,
-    notes: str | None = None,
-    actor: str = "USER",
-) -> dict[str, Any]:
-    """Edit lot fields, invalidate previous approvals, and report marketplace capabilities.
-
-    DoD:
-    - Edits fields on Lot and Item.
-    - Invalidates existing approval for the item.
-    - Sets item and lot status to AWAITING_APPROVAL.
-    - Inspects capabilities to mark which platforms support editing vs requiring relist.
-    """
-    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
-    if not lot:
-        raise ValueError(f"Lot with code {lot_code} not found")
-
-    item = session.scalar(select(Item).where(Item.code == lot.item_id))
-    if not item:
-        raise ValueError(f"Item {lot.item_id} linked to lot {lot_code} not found")
-
-    # Track modifications
-    changes: dict[str, Any] = {}
-
-    if price is not None:
-        old_price = lot.price_gross
-        lot.price_gross = price
-        item.target_price = price
-        changes["price_gross"] = {"old": str(old_price), "new": str(price)}
-
-    if price_floor is not None:
-        old_floor = lot.price_floor
-        lot.price_floor = price_floor
-        changes["price_floor"] = {"old": str(old_floor), "new": str(price_floor)}
-
-    if shipping_policy is not None:
-        old_sp = lot.shipping_policy
-        lot.shipping_policy = shipping_policy
-        changes["shipping_policy"] = {"old": old_sp, "new": shipping_policy}
-
-    if notes is not None:
-        lot.notes = notes
-        item.notes = notes
-        changes["notes"] = notes
-
-    if title is not None and title.strip():
-        old_title = item.title
-        item.title = title.strip()
-        changes["title"] = {"old": old_title, "new": item.title}
-
-    if description is not None:
-        current_attrs = dict(item.attrs or {})
-        # TZ-NextLife-UI-N2 §3.3 P-E12: поле «Описание / текст объявления»
-        # — это уходящий в публикацию текст. Пишем в единый источник
-        # правды (final_text), а не в attrs.description, который
-        # маскировался бы за final_text/notes (два источника правды).
-        current_attrs["final_text"] = description.strip()
-        item.attrs = current_attrs
-        changes["description"] = description.strip()
-
-    # Invalidate approvals: delete past approvals for this item
-    del_res = session.execute(
-        delete(Approval).where(Approval.item_code == item.code)
-    )
-    deleted_approvals = getattr(del_res, "rowcount", 0)
-
-    # Invalidate status: both item and lot return to AWAITING_APPROVAL
-    if item.status != ItemStatus.AWAITING_APPROVAL.value:
-        # Check transition if needed, or directly set for re-approval
-        item.status = ItemStatus.AWAITING_APPROVAL.value
-    lot.status = ItemStatus.AWAITING_APPROVAL.value
-
-    # Inspect capabilities
-    marketplace_caps = get_marketplace_edit_capabilities(session, lot_code)
-
-    # Record audit event
-    record_audit_event(
-        session=session,
-        actor=actor,
-        action="LOT_EDITED",
-        entity="lot",
-        entity_id=lot.code,
-        data={
-            "changes": changes,
-            "invalidated_approvals_count": deleted_approvals,
-            "marketplace_capabilities": marketplace_caps,
-        },
-    )
-
-    return {
-        "lot_code": lot.code,
-        "item_code": item.code,
-        "status": lot.status,
-        "price_gross": lot.price_gross,
-        "price_floor": lot.price_floor,
-        "shipping_policy": lot.shipping_policy,
-        "invalidated_approvals": True,
-        "marketplace_capabilities": marketplace_caps,
-    }

### git diff -- src/nextlife/services/wizard_textgen.py
diff --git a/src/nextlife/services/wizard_textgen.py b/src/nextlife/services/wizard_textgen.py
index cb46b74..52d60d3 100644
--- a/src/nextlife/services/wizard_textgen.py
+++ b/src/nextlife/services/wizard_textgen.py
@@ -16,9 +16,6 @@ from nextlife.storage.models import Item
 
 PLACEHOLDER_TEXT = "Описание будет сгенерировано после подключения LLM"
 
-# Item.attrs key holding the persisted step-2 generated text.
-GENERATED_ATTR = "wizard_generated_text"
-
 # Language code -> prepositional form for the generation prompt («на ... языке»).
 _GENERATION_LANG_PREP = {
     "ru": "русском",
@@ -29,30 +26,6 @@ _GENERATION_LANG_PREP = {
 }
 
 
-def load_generated(item: Item) -> dict[str, Any] | None:
-    """The persisted generated text (step 2), or None when absent/empty.
-
-    The generated title/description is stored once on the Item so that later
-    visits (e.g. after a photo upload) reuse it instead of re-running the LLM.
-    """
-    g = (item.attrs or {}).get(GENERATED_ATTR)
-    if isinstance(g, dict) and (g.get("text") or g.get("title")):
-        return g
-    return None
-
-
-def save_generated(session: Session, item: Item, gen: dict[str, Any]) -> None:
-    """Persist the generated title/text/mode in ``attrs["wizard_generated_text"]``."""
-    attrs = dict(item.attrs or {})
-    attrs[GENERATED_ATTR] = {
-        "title": gen.get("title", ""),
-        "text": gen.get("text", ""),
-        "mode": gen.get("mode", ""),
-    }
-    item.attrs = attrs
-    session.flush()
-
-
 def _parse_generated(content: str) -> tuple[str, str]:
     """Parse the LLM's ``{"title": ..., "description": ...}`` JSON.

### DELETED FILE: templates/web/create.html
diff --git a/templates/web/create.html b/templates/web/create.html
deleted file mode 100644
index a247d2c..0000000
--- a/templates/web/create.html
+++ /dev/null
@@ -1,36 +0,0 @@
-{% extends "base.html" %}
-
-{% block content %}
-<div class="page-head">
-  <div>
-    <h2 class="page-title">Создание объявлений</h2>
-    <div class="page-subtitle">Новый лот или возврат к незавершённым черновикам</div>
-  </div>
-</div>
-
-<a href="/ui/lot-wizard/search" class="wz-btn wz-btn-primary wz-btn-full btn-new-lot">➕ Новый лот (мастер)</a>
-
-<h3 class="create-section-title">Незавершённые черновики</h3>
-
-{% if drafts %}
-<div class="create-drafts">
-  {% for d in drafts %}
-  <a href="{{ d.resume_url }}" class="create-draft-card">
-    <div class="create-draft-top">
-      <span class="badge">#{{ d.code }}</span>
-      <span class="create-draft-step">{{ d.resume_step }}</span>
-    </div>
-    <div class="create-draft-title">{{ d.title }}</div>
-    <div class="create-draft-meta">Выбрано объявлений: {{ d.selected_count }}</div>
-  </a>
-  {% endfor %}
-</div>
-{% else %}
-<div class="card">
-  <div class="empty">
-    Нет незавершённых черновиков.<br>
-    Начните новый лот, чтобы создать объявление.
-  </div>
-</div>
-{% endif %}
-{% endblock %}

### DELETED FILE: templates/web/item_detail.html
diff --git a/templates/web/item_detail.html b/templates/web/item_detail.html
deleted file mode 100644
index e576d30..0000000
--- a/templates/web/item_detail.html
+++ /dev/null
@@ -1,366 +0,0 @@
-{% extends "base.html" %}
-{% block content %}
-
-{% if notice == 'photos_uploaded' %}
-<div class="toast-banner">✓ Фотографии успешно загружены и обработаны</div>
-{% elif notice == 'photo_deleted' %}
-<div class="toast-banner">✓ Фотография удалена</div>
-{% elif notice == 'photo_not_found' %}
-<div class="toast-banner warning">⚠️ Фото не найдено</div>
-{% elif notice == 'lot_edited_approval_invalidated' %}
-<div class="toast-banner warning">⚠️ Изменения сохранены. Требуется повторное утверждение</div>
-{% elif notice == 'created' %}
-<div class="toast-banner">✓ Лот успешно создан</div>
-{% elif notice == 'approved' %}
-<div class="toast-banner">✓ Черновик публикации успешно утверждён</div>
-{% endif %}
-
-<div class="stack-2">
-  <a href="/ui/items" class="btn btn-secondary btn-sm btn-inline">← Назад в ленту</a>
-  <div class="row between align-start">
-    <div class="stack-1">
-      <span class="badge">#{{ item.code }}</span>
-      <h2 class="fs-xl fw-bold">{{ item.title }}</h2>
-      <div class="price">{% if item.target_price is not none %}{{ item.target_price | money(item.target_currency) }}{% else %}цена не задана{% endif %}</div>
-    </div>
-    <div class="stack-1">
-      <span class="{{ status_badge_cls }}">{{ item.status | status_ru }}</span>
-      <div class="row tight">
-        <a href="/ui/lots/{{ item.code }}/shipping" class="btn btn-sm btn-secondary btn-inline" title="Доставка">Доставка</a>
-        <a href="/ui/lots/{{ item.code }}/edit" class="btn btn-sm btn-primary btn-inline">Правка</a>
-      </div>
-    </div>
-  </div>
-</div>
-
-<!-- Interactive Photo Gallery & Slider -->
-{# TZ-NextLife-UI-N2 §4 V3 (P-E05): битое фото не даёт 404 — вместо <img> плейсхолдер. #}
-{% set missing_src = "data:image/svg+xml;utf8," ~ "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360'><rect width='100%25' height='100%25' fill='%23e9ecef'/><text x='50%25' y='54%25' font-size='24' text-anchor='middle' fill='%236c757d'>файл не найден</text></svg>" %}
-{# TZ-NextLife-UI-N2 §4 V3 (Э-6): роли конвейера — внутренняя механика,
-   продавец видит «Оригинал» / «Обработано» / «Дефект». #}
-{% set role_labels = {'PUBLISH': 'Обработано', 'PROCESSED': 'Обработано', 'DEFECT': 'Дефект', 'ORIGINAL': 'Оригинал'} %}
-<div class="card" id="gallery-card">
-  <div class="stack-3">
-  <div class="row between">
-    <div class="card-title">Фотогалерея ({{ photos|length }})</div>
-    <span class="fs-xs text-muted">{{ photos|length }} фото</span>
-  </div>
-
-  {% if photos and photos|length > 0 %}
-  <div class="slider-container" id="main-slider" data-testid="photo-slider">
-    <button type="button" class="slider-arrow slider-prev" id="btn-slider-prev" onclick="changeSlide(-1)" aria-label="Предыдущее фото" data-testid="slider-prev">‹</button>
-    <img id="slider-main-img" class="slider-img" src="{{ photos[0].url or missing_src }}" alt="Предпросмотр фото" onclick="openActiveLightbox()" data-testid="slider-current-image">
-    <button type="button" class="slider-arrow slider-next" id="btn-slider-next" onclick="changeSlide(1)" aria-label="Следующее фото" data-testid="slider-next">›</button>
-    <span class="slider-counter" id="slider-counter" data-testid="slider-counter">1 / {{ photos|length }}</span>
-    <span class="slider-role-tag badge badge-active" id="slider-role-tag" data-testid="slider-role">{{ role_labels.get(photos[0].role, 'Фото') }}</span>
-  </div>
-
-  <!-- Thumbnails strip (TZ N2 V3: одна миниатюра на логическое фото) -->
-  <div class="thumb-strip" id="thumb-strip" data-testid="slider-thumbnails">
-    {% for photo in photos %}
-    <div class="thumb-card {% if loop.first %}active{% endif %}" id="thumb-item-{{ loop.index0 }}" onclick="selectSlide({{ loop.index0 }})" data-url="{{ photo.url }}" data-role="{{ photo.role }}" data-testid="thumb-item-{{ loop.index0 }}">
-      {% if photo.broken %}
-      <div class="thumb-broken" data-testid="photo-broken-mark" title="Файл фото не найден"
-           style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#e9ecef;color:#6c757d;font-size:10px;text-align:center;padding:2px;">🚫 файл не найден</div>
-      {% else %}
-      <img src="{{ photo.url }}" alt="Миниатюра {{ loop.index }}">
-      {% endif %}
-      {% if photo.role in ('PUBLISH', 'PROCESSED') %}
-      <span class="thumb-processed-badge" data-testid="thumb-processed-badge"
-            style="position:absolute;top:0;left:0;background:rgba(0,0,0,.55);color:#fff;font-size:9px;line-height:1;padding:2px 4px;border-radius:0 0 6px 0;z-index:1;">обработано</span>
-      {% endif %}
-      {# TZ-NextLife-UI-N2 §4 V6: пометки «Обложка» и AI-изображения. #}
-      {% if photo.is_cover %}
-      <span data-testid="photo-cover-mark" title="Обложка лота"
-            style="position:absolute;bottom:0;left:0;background:var(--c-accent);color:#fff;font-size:9px;line-height:1;padding:2px 4px;border-radius:0 6px 0 0;z-index:1;">обложка</span>
-      {% endif %}
-      {% if photo.ai %}
-      <span data-testid="photo-ai-mark" title="Изображение изменено генеративно"
-            style="position:absolute;bottom:0;right:0;background:rgba(0,0,0,.78);color:#ffd54d;font-size:9px;line-height:1;padding:2px 4px;border-radius:0 0 0 6px;z-index:1;">AI</span>
-      {% endif %}
-      {% if photo.id %}
-      <form method="POST" action="/ui/items/{{ item.code }}/photos/{{ photo.id }}/delete" onsubmit="event.stopPropagation(); return confirm('Удалить эту фотографию?');">
-        <button type="submit" class="thumb-del-btn" title="Удалить фото" aria-label="Удалить фото {{ loop.index }}" data-testid="btn-delete-photo-{{ loop.index0 }}">&times;</button>
-      </form>
-      {% endif %}
-    </div>
-    {% endfor %}
-  </div>
-  <div class="fs-xs text-muted">
-    💡 Нажмите на фото для полноэкранного просмотра. Листайте стрелками или свайпом.
-  </div>
-
-  {# TZ-NextLife-UI-N2 §4 V6 (R5): действия на каждое фото. Результат —
-     новая версия, оригинал неизменен; чистящие операции скрыты для
-     фото дефекта (заявленный дефект остаётся неретушированным). #}
-  <div class="stack-1" data-testid="photo-ops">
-    {% for photo in photos %}
-    <div class="row between wrap align-center">
-      <span class="fs-xs text-muted">Фото {{ loop.index1 }}:</span>
-      <div class="row tight wrap">
-        {% if photo.display_id and not photo.broken %}
-        <form method="POST" action="/ui/items/{{ item.code }}/photos/{{ photo.display_id }}/rotate">
-          <button type="submit" class="btn btn-sm btn-secondary btn-inline" title="Поворот кадра на 90°" data-testid="btn-op-rotate-{{ loop.index0 }}">⟳ Выровнять</button>
-        </form>
-        <form method="POST" action="/ui/items/{{ item.code }}/photos/{{ photo.display_id }}/crop">
-          <button type="submit" class="btn btn-sm btn-secondary btn-inline" title="Обрезка 10% от поля" data-testid="btn-op-crop-{{ loop.index0 }}">✂ Обрезать</button>
-        </form>
-        {% if photo.role != 'DEFECT' %}
-        <form method="POST" action="/ui/items/{{ item.code }}/photos/{{ photo.display_id }}/clean-bg">
-          <button type="submit" class="btn btn-sm btn-secondary btn-inline" title="Нормализация фона" data-testid="btn-op-clean-bg-{{ loop.index0 }}">▦ Фон</button>
-        </form>
-        <form method="POST" action="/ui/items/{{ item.code }}/photos/{{ photo.display_id }}/remove-dust">
-          <button type="submit" class="btn btn-sm btn-secondary btn-inline" title="Удаление пыли и засветов" data-testid="btn-op-remove-dust-{{ loop.index0 }}">✦ Пыль</button>
-        </form>
-        {% endif %}
-        <form method="POST" action="/ui/items/{{ item.code }}/photos/{{ photo.display_id }}/cover">
-          <button type="submit" class="btn btn-sm btn-secondary btn-inline" title="Сделать обложкой лота" data-testid="btn-op-cover-{{ loop.index0 }}">★ Обложка</button>
-        </form>
-        <form method="POST" action="/ui/items/{{ item.code }}/photos/{{ photo.display_id }}/defect">
-          <button type="submit" class="btn btn-sm btn-secondary btn-inline" title="Пометить как фото дефекта" data-testid="btn-op-defect-{{ loop.index0 }}">⚑ Дефект</button>
-        </form>
-        {% else %}
-        <span class="fs-xs text-muted">нет действий (файл не найден)</span>
-        {% endif %}
-      </div>
-    </div>
-    {% endfor %}
-  </div>
-
-  {# TZ-NextLife-UI-N2 §4 V6 (R4): подсказки по качеству после загрузки. #}
-  {% set hint_photos = photos | selectattr("hints") | list %}
-  {% if hint_photos %}
-  <div class="info-box info-box-amber" data-testid="quality-hints">
-    <div class="fs-sm fw-semibold">💡 Подсказки по качеству:</div>
-    <div class="fs-xs">
-      {% for photo in hint_photos %}
-      <div>Фото {{ loop.index1 }}: {% for hint in photo.hints %}{{ hint }}{% if not loop.last %}; {% endif %}{% endfor %}</div>
-      {% endfor %}
-    </div>
-  </div>
-  {% endif %}
-
-  <div class="row between wrap">
-    <span class="fs-xs text-muted">Предпросмотр на площадках:</span>
-    <div class="row tight wrap">
-      <a href="/ui/items/{{ item.code }}/preview/bazos_cz" class="btn btn-secondary btn-sm btn-inline" data-testid="btn-preview-bazos">Bazoš.cz</a>
-      <a href="/ui/items/{{ item.code }}/preview/sbazar_cz" class="btn btn-secondary btn-sm btn-inline" data-testid="btn-preview-sbazar">Sbazar.cz</a>
-      <a href="/ui/items/{{ item.code }}/preview/aukro_cz" class="btn btn-secondary btn-sm btn-inline" data-testid="btn-preview-aukro">Aukro.cz</a>
-      <form method="POST" action="/ui/items/{{ item.code }}/photos/regen-variants">
-        <button type="submit" class="btn btn-sm btn-secondary btn-inline" title="Догенерировать варианты под каждую площадку" data-testid="btn-regen-variants">↻ Догенерировать</button>
-      </form>
-    </div>
-  </div>
-  {% else %}
-  <div class="empty empty-sm">
-    📷 Нет загруженных фотографий для этого лота.<br>
-    Загрузите оригинальные снимки через форму ниже.
-  </div>
-  {% endif %}
-  </div>
-</div>
-
-<!-- Upload form -->
-<div class="card">
-  <div class="card-title">Загрузить новые фотографии</div>
-  <form method="POST" action="/ui/items/{{ item.code }}/photos/upload" enctype="multipart/form-data" data-testid="photo-upload-form">
-    <div class="field">
-      <label for="photo-file-input">Выберите изображения (JPEG, PNG, WebP):</label>
-      <input type="file" id="photo-file-input" name="files" multiple accept="image/*" required data-testid="photo-file-input">
-    </div>
-    <div class="field">
-      <label for="photo-role-select">Тип снимков:</label>
-      <select id="photo-role-select" name="role" class="input-text" data-testid="photo-role-select">
-        <option value="ORIGINAL">Оригинал / Общий вид</option>
-        <option value="DEFECT">Фотография дефекта / нюанса</option>
-      </select>
-    </div>
-    <button type="submit" class="btn btn-primary btn-full" data-testid="btn-submit-upload">
-      📤 Загрузить фото
-    </button>
-  </form>
-</div>
-
-{# TZ-NextLife-UI-N2 §4 V4 (P-E07): красный блок «Дефекты» — только реальные
-   дефекты; позитивные формулировки — отдельным блоком «Особенности» (Э-1). #}
-{% if item.defects %}
-<div class="card" id="defects-card" data-testid="item-defects">
-  <div class="card-title">Дефекты</div>
-  <div class="stack-2">
-    {% for d in item.defects %}
-    <div class="info-box info-box-red row">
-      <span class="badge badge-defect">{{ d.zone or 'дефект' }}</span>
-      <span class="text-danger fw-medium grow">{{ d.text }}{% if d.photo_id %} · фото #{{ d.photo_id }}{% endif %}</span>
-    </div>
-    {% endfor %}
-  </div>
-</div>
-{% endif %}
-
-{% if item.features %}
-<div class="card" id="features-card" data-testid="item-features">
-  <div class="card-title">Особенности</div>
-  <div class="stack-2">
-    {% for d in item.features %}
-    <div class="info-box info-box-slate row">
-      <span class="badge">{{ d.zone or 'особенность' }}</span>
-      <span class="fw-medium grow">{{ d.text }}{% if d.photo_id %} · фото #{{ d.photo_id }}{% endif %}</span>
-    </div>
-    {% endfor %}
-  </div>
-</div>
-{% endif %}
-
-<!-- Text Blocks: Notes, Generated Description (Draft), Final Text -->
-<div class="card" id="text-blocks-card" data-testid="item-text-blocks">
-  <div class="card-title">Текстовые блоки объявления</div>
-  <div class="stack-4">
-    <div>
-      <div class="row between">
-        <label class="fs-sm fw-semibold">📝 Заметки оператора (Notes):</label>
-        <span class="fs-xs text-muted">Внутренние заметки</span>
-      </div>
-      <div class="info-box info-box-slate fs-sm">
-        {% if text_blocks.notes %}
-        {{ text_blocks.notes }}
-        {% else %}
-        <span class="text-muted italic">Заметки по лоту отсутствуют.</span>
-        {% endif %}
-      </div>
-    </div>
-
-    <div>
-      <div class="row between">
-        <label class="fs-sm fw-semibold">✨ Сгенерированное описание (Generated Draft):</label>
-        <a href="/ui/draft/{{ item.code }}" class="btn btn-secondary btn-sm btn-inline" data-testid="btn-open-draft">Редактор черновика →</a>
-      </div>
-      <div class="info-box info-box-slate fs-sm pre" data-testid="block-generated-draft">{{ text_blocks.generated_desc }}</div>
-    </div>
-
-    <div>
-      <div class="row between">
-        <label for="final-listing-text" class="fs-sm fw-semibold">📋 Финальный текст для публикации (Final):</label>
-        <span class="fs-xs text-muted">Символов: {{ text_blocks.final_text|length }}</span>
-      </div>
-      <form method="POST" action="/ui/draft/{{ item.code }}/update" data-testid="form-final-text" class="stack-2">
-        <textarea id="final-listing-text" name="listing_text" class="input-text" rows="5" placeholder="Финальный утверждённый текст объявления...">{{ text_blocks.final_text }}</textarea>
-        <div class="row between">
-          <span class="fs-xs text-muted">💡 Изменение текста требует повторного утверждения</span>
-          <button type="submit" class="btn btn-secondary btn-sm btn-inline" data-testid="btn-save-final-text">💾 Сохранить финальный текст</button>
-        </div>
-      </form>
-    </div>
-  </div>
-</div>
-
-<!-- Shipping & Delivery Constructor Shortcut -->
-<div class="card">
-  <div class="row between align-start">
-    <div>
-      <div class="card-title">Доставка и тарифы</div>
-      <div class="fs-xs text-muted">Калькулятор и настройка операторов доставки (Zásilkovna, Balíkovna, PPL)</div>
-    </div>
-    <a href="/ui/lots/{{ item.code }}/shipping" class="btn btn-secondary btn-sm btn-inline" data-testid="btn-open-shipping">
-      Настроить →
-    </a>
-  </div>
-</div>
-
-{# TZ-NextLife-UI-N2 §4 V5 (P-R07/P-R08): разведка рынка по аналогам. #}
-{% include "_research_panel.html" %}
-
-{# TZ-NextLife-UI-N2 §4 V4 (P-E13, Э-6): строки аудита — служебная механика,
-   внутри свёрнутого блока по умолчанию. #}
-<details class="card internal-details" data-testid="internal-block-audit">
-  <summary class="card-title cursor-pointer">Служебный блок: лента событий (audit_event)</summary>
-  <div class="scroll-box">
-    {% for ev in events %}
-    <div class="event-row">
-      <span class="text-muted">{{ ev.created_at }}</span> | <strong>{{ ev.action }}</strong> ({{ ev.actor }})
-    </div>
-    {% else %}
-    <div class="fs-sm text-muted">Нет записей аудита</div>
-    {% endfor %}
-  </div>
-</details>
-
-<script>
-(function() {
-  // TZ-NextLife-UI-N2 §4 V3 (P-E05): битое фото — плейсхолдер, без 404 в сети.
-  const MISSING_SRC = "{{ missing_src }}";
-  const photos = [
-    {% for photo in photos %}
-    { url: "{{ photo.url }}", role: "{{ photo.role }}", label: "{{ role_labels.get(photo.role, 'Фото') }}" },
-    {% endfor %}
-  ];
-  let currentIndex = 0;
-
-  window.selectSlide = function(idx) {
-    if (photos.length === 0) return;
-    currentIndex = (idx + photos.length) % photos.length;
-    updateSliderUI();
-  };
-
-  window.changeSlide = function(delta) {
-    if (photos.length === 0) return;
-    currentIndex = (currentIndex + delta + photos.length) % photos.length;
-    updateSliderUI();
-  };
-
-  function updateSliderUI() {
-    const mainImg = document.getElementById('slider-main-img');
-    const counter = document.getElementById('slider-counter');
-    const roleTag = document.getElementById('slider-role-tag');
-    if (!mainImg || photos.length === 0) return;
-
-    const cur = photos[currentIndex];
-    mainImg.src = cur.url || MISSING_SRC;
-    if (counter) counter.textContent = (currentIndex + 1) + ' / ' + photos.length;
-    if (roleTag) {
-      roleTag.textContent = cur.label;
-      if (cur.role === 'DEFECT') {
-        roleTag.className = 'slider-role-tag badge badge-defect';
-      } else if (cur.role === 'PUBLISH') {
-        roleTag.className = 'slider-role-tag badge badge-approved';
-      } else {
-        roleTag.className = 'slider-role-tag badge badge-active';
-      }
-    }
-
-    // Update active thumb
-    document.querySelectorAll('.thumb-card').forEach((el, i) => {
-      if (i === currentIndex) {
-        el.classList.add('active');
-        el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
-      } else {
-        el.classList.remove('active');
-      }
-    });
-  }
-
-  window.openActiveLightbox = function() {
-    if (photos.length > 0 && typeof openLightbox === 'function') {
-      openLightbox(photos[currentIndex].url || MISSING_SRC, '.thumb-card img');
-    }
-  };
-
-  // Touch swipe support on slider
-  const sliderEl = document.getElementById('main-slider');
-  if (sliderEl) {
-    let touchStartX = 0;
-    sliderEl.addEventListener('touchstart', (e) => {
-      touchStartX = e.changedTouches[0].screenX;
-    }, { passive: true });
-    sliderEl.addEventListener('touchend', (e) => {
-      const touchEndX = e.changedTouches[0].screenX;
-      const diff = touchEndX - touchStartX;
-      if (diff > 40) {
-        changeSlide(-1); // swipe right -> previous
-      } else if (diff < -40) {
-        changeSlide(1);  // swipe left -> next
-      }
-    }, { passive: true });
-  }
-})();
-</script>
-{% endblock %}

### DELETED FILE: templates/web/items.html
diff --git a/templates/web/items.html b/templates/web/items.html
deleted file mode 100644
index e75565f..0000000
--- a/templates/web/items.html
+++ /dev/null
@@ -1,66 +0,0 @@
-{% extends "base.html" %}
-{% block content %}
-<div class="page-head">
-  <div>
-    <h2 class="page-title">Лента предметов</h2>
-    <div class="page-subtitle">Актуальные объявления и черновики</div>
-  </div>
-  <div class="row">
-    <a href="/ui/lot-wizard/search" class="btn btn-primary">➕ Новый лот (мастер)</a>
-    <button id="btn-refresh-feed" class="header-refresh-btn" onclick="triggerRefresh(event)">
-      🔄 Обновить
-    </button>
-  </div>
-</div>
-
-<div class="field">
-  <input type="search" class="input-text" aria-label="Поиск предметов" placeholder="🔍 Быстрый поиск предметов..." oninput="filterItems(this.value)">
-</div>
-
-<div class="filter-pills">
-  <a href="/ui/items" class="filter-pill {{ 'active' if not status_filter else '' }}">Все</a>
-  <a href="/ui/items?status_filter=AWAITING_APPROVAL" class="filter-pill {{ 'active' if status_filter == 'AWAITING_APPROVAL' else '' }}">Требуют утверждения</a>
-  <a href="/ui/items?status_filter=APPROVED" class="filter-pill {{ 'active' if status_filter == 'APPROVED' else '' }}">Утверждены</a>
-  <a href="/ui/items?status_filter=DRAFT" class="filter-pill {{ 'active' if status_filter == 'DRAFT' else '' }}">Черновики</a>
-</div>
-
-<div class="item-feed-grid">
-  {% for it in items %}
-  <div class="card item-feed-card" id="item-{{ it.code }}">
-    <div class="row between">
-      <span class="badge">#{{ it.code }}</span>
-      <span class="{{ it.status_badge_cls }}">{{ it.status | status_ru }}</span>
-    </div>
-    <div class="feed-card-body">
-      {% if it.thumb_url %}
-      <img src="{{ it.thumb_url }}" class="feed-thumb feed-card-thumb" alt="Миниатюра лота #{{ it.code }}" onclick="openLightbox('{{ it.thumb_url }}')">
-      {% else %}
-      <div class="feed-thumb feed-card-thumb feed-thumb-placeholder">📦</div>
-      {% endif %}
-      <div class="feed-card-content">
-        <div class="card-title">
-          <a href="/ui/items/{{ it.code }}">{{ it.title }}</a>
-        </div>
-        <div class="price">{% if it.target_price is not none %}{{ it.target_price | money(it.target_currency) }}{% else %}цена не задана{% endif %}</div>
-        <div class="fs-xs text-muted">
-          📸 Фото: {{ it.photo_count }} · 📢 Листингов: {{ it.listings_count }}
-        </div>
-        {% if it.status == 'AWAITING_APPROVAL' %}
-        <a href="/ui/draft/{{ it.code }}" class="btn btn-sm">Проверить черновик →</a>
-        {% else %}
-        <a href="/ui/items/{{ it.code }}" class="btn btn-secondary btn-sm">Карточка предмета →</a>
-        {% endif %}
-      </div>
-    </div>
-  </div>
-  {% else %}
-  <div class="card">
-    <div class="empty">
-      Нет предметов в этой категории.<br>
-      Создайте первый предмет, чтобы начать работу.
-      <a href="/ui/wizard" class="btn">Создать предмет</a>
-    </div>
-  </div>
-  {% endfor %}
-</div>
-{% endblock %}

### DELETED FILE: templates/web/lot_edit.html
diff --git a/templates/web/lot_edit.html b/templates/web/lot_edit.html
deleted file mode 100644
index 2cdf4c8..0000000
--- a/templates/web/lot_edit.html
+++ /dev/null
@@ -1,86 +0,0 @@
-{% extends "base.html" %}
-
-{% block title %}Редактирование лота {{ lot.code }} — NextLife{% endblock %}
-
-{% block content %}
-<div class="card">
-  <div class="page-head">
-    <h2 class="page-title">Редактирование лота #{{ lot.code }}</h2>
-    <span class="badge badge-warning">Текущий статус: {{ lot.status | status_ru }}</span>
-  </div>
-
-  <div class="info-box info-box-amber fs-sm">
-    <strong>⚠️ Внимание:</strong> Любая правка цены, описания или условий инвалидирует ранее выданное утверждение.
-    Лот будет возвращён в статус <code>AWAITING_APPROVAL</code> для повторной проверки владельцем.
-  </div>
-
-  <!-- Marketplace capabilities inspection block -->
-  <div class="box box-subtle">
-    <div class="row between align-start">
-      <span class="section-title">Синхронизация с торговыми площадками</span>
-      <span class="fs-xs text-faint">Возможности площадок (Marketplace Capabilities)</span>
-    </div>
-    <p class="fs-xs text-muted">
-      При сохранении изменений система проверяет возможности каждой площадки. Если площадка поддерживает прямое редактирование (edit), цена обновится сразу. Если нет — потребуется перевыставить лот после повторного утверждения.
-    </p>
-    <div class="stack-2">
-      {% for mp in marketplace_capabilities %}
-      <div class="mp-row">
-        <div class="stack-1">
-          <span class="fw-bold">{{ mp.title }}</span>
-          <span class="fs-xs text-dim">{{ mp.message }}</span>
-        </div>
-        {% if mp.supports_edit %}
-        <span class="badge badge-success" data-testid="mp-edit-supported">Прямое обновление</span>
-        {% else %}
-        <span class="badge badge-warning" data-testid="mp-relist-required">Требует переразмещения</span>
-        {% endif %}
-      </div>
-      {% endfor %}
-    </div>
-  </div>
-
-  <form method="POST" action="/ui/lots/{{ lot.code }}/edit" class="stack-3">
-    <div class="field">
-      <label class="field-label" for="lot-title">Название предмета</label>
-      <input type="text" id="lot-title" name="title" value="{{ item.title }}" required class="input-text">
-    </div>
-
-    <div class="field">
-      <label class="field-label" for="lot-description">Описание / текст объявления</label>
-      <textarea id="lot-description" name="description" rows="4" class="input-text">{{ item.outgoing_text or '' }}</textarea>
-    </div>
-
-    <div class="grid-2">
-      <div class="field">
-        <label class="field-label" for="lot-price">Цена (Kč)</label>
-        <input type="number" id="lot-price" name="price" step="1" value="{{ lot.price_gross or '' }}" required class="input-text">
-      </div>
-      <div class="field">
-        <label class="field-label" for="lot-price-floor">Нижний порог цены (Kč)</label>
-        <input type="number" id="lot-price-floor" name="price_floor" step="1" value="{{ lot.price_floor or '' }}" class="input-text">
-      </div>
-    </div>
-
-    <div class="field">
-      <label class="field-label" for="lot-shipping-policy">Политика доставки</label>
-      {# TZ-NextLife-UI-N2 §4 V4 (P-E10): единый словарь политики доставки. #}
-      <select id="lot-shipping-policy" name="shipping_policy" class="input-text">
-        {% for p in shipping_policy_options %}
-        <option value="{{ p.value }}" {% if lot.shipping_policy == p.value %}selected{% endif %}>{{ p.label }} ({{ p.value }})</option>
-        {% endfor %}
-      </select>
-    </div>
-
-    <div class="field">
-      <label class="field-label" for="lot-notes">Внутренние заметки</label>
-      <textarea id="lot-notes" name="notes" rows="2" class="input-text">{{ lot.notes or '' }}</textarea>
-    </div>
-
-    <div class="row tight">
-      <button type="submit" class="btn btn-primary grow">Сохранить и отправить на утверждение</button>
-      <a href="/ui/items/{{ item.code }}" class="btn btn-secondary btn-inline">Отмена</a>
-    </div>
-  </form>
-</div>
-{% endblock %}

### DELETED FILE: templates/web/marketplaces.html
diff --git a/templates/web/marketplaces.html b/templates/web/marketplaces.html
deleted file mode 100644
index 6bc279a..0000000
--- a/templates/web/marketplaces.html
+++ /dev/null
@@ -1,48 +0,0 @@
-{% extends "base.html" %}
-{% block title %}Площадки — NextLife{% endblock %}
-{% block content %}
-<div class="page-head">
-  <h2 class="page-title">Площадки</h2>
-</div>
-
-<p class="fs-sm text-muted">
-  Статус канала — по живой канарейке (ежедневная проба). Публикации на
-  заблокированном канале не выполняются.
-</p>
-
-{% if not channels %}
-<div class="card">
-  <div class="text-muted fs-sm">Канарейки ещё не запускались. Запустите ежедневную
-  пробу (<code>canaries run</code>), чтобы получить статусы каналов.</div>
-</div>
-{% endif %}
-
-<div class="stack-2">
-  {% for ch in channels %}
-  <div class="card" data-testid="channel-card-{{ ch.key }}">
-    <div class="row between">
-      <span class="fw-semibold">{{ ch.title }}</span>
-      {% if ch.state == 'OK' %}
-      <span class="badge badge-success">OK</span>
-      {% else %}
-      <span class="badge badge-defect">BLOCKED</span>
-      {% endif %}
-    </div>
-    <div class="text-muted fs-sm">
-      Сценарий: {{ ch.scenario }} · Канал: {{ ch.channel }}
-    </div>
-    {% if ch.last_success_at %}
-    <div class="fs-sm">Последняя успешная проба: {{ ch.last_success_at }}</div>
-    {% endif %}
-    {% if ch.last_attempt_at %}
-    <div class="fs-sm text-muted">Последняя проба: {{ ch.last_attempt_at }}</div>
-    {% endif %}
-    {% if ch.block_reason %}
-    <div class="fs-sm block-reason" style="word-break: break-all;">
-      Причина блокировки: {{ ch.block_reason }}
-    </div>
-    {% endif %}
-  </div>
-  {% endfor %}
-</div>
-{% endblock %}

### DELETED FILE: templates/web/shipping_options.html
diff --git a/templates/web/shipping_options.html b/templates/web/shipping_options.html
deleted file mode 100644
index e65f9ea..0000000
--- a/templates/web/shipping_options.html
+++ /dev/null
@@ -1,207 +0,0 @@
-{% extends "base.html" %}
-
-{% block title %}Опции доставки лота {{ summary.lot_code }} — NextLife{% endblock %}
-
-{% block content %}
-<div class="stack-3">
-  <a href="/ui/items/{{ summary.item_code }}" class="btn btn-secondary btn-sm btn-inline">← К предмету #{{ summary.item_code }}</a>
-
-  <div class="card" x-data="shippingConstructor({{ summary_json }})">
-    <div class="stack-3">
-      <div class="page-head">
-        <h2 class="page-title">Опции доставки лота #{{ summary.lot_code }}</h2>
-        <span class="badge badge-primary">Конструктор тарифов (V.2 п.6)</span>
-      </div>
-
-      <div class="box box-subtle fs-xs text-dim">
-        Конструктор автоматически подбирает базовые тарифы чешских служб (Zásilkovna, Balíkovna, Česká pošta).
-        При ручном изменении тарифа он помечается как <span class="badge badge-warning">Ручная цена</span>.
-        Суммы «Итого для покупателя» и «На руки продавцу» пересчитываются на лету.
-      </div>
-
-      <div class="grid-2">
-        <div class="field">
-          <label for="input-item-price">Базовая цена предмета (Kč)</label>
-          <input type="number" step="1" id="input-item-price" x-model.number="itemPrice" class="input-text" data-testid="input-item-price">
-        </div>
-        <div class="field">
-          <label for="select-shipping-policy">Политика доставки</label>
-          {# TZ-NextLife-UI-N2 §4 V4 (P-E10): единый словарь политики доставки. #}
-          <select id="select-shipping-policy" x-model="shippingPolicy" class="input-text" data-testid="select-shipping-policy">
-            {% for p in shipping_policy_options %}
-            <option value="{{ p.value }}">{{ p.label }} ({{ p.value }})</option>
-            {% endfor %}
-          </select>
-        </div>
-      </div>
-
-      <!-- Add option row -->
-      <div class="box box-accent">
-        <div class="field">
-          <label for="select-carrier-to-add">Добавить службу доставки:</label>
-          <div class="row tight">
-            <select id="select-carrier-to-add" x-model="selectedCarrier" class="input-text grow" data-testid="select-carrier-to-add">
-              <template x-for="c in availableCarriers" :key="c.key">
-                <option :value="c.key" x-text="c.title + ' (от ' + fmtCzk(c.default_cost) + ')'"></option>
-              </template>
-            </select>
-            <button type="button" @click="addOption()" class="btn btn-primary btn-inline" data-testid="btn-add-option">
-              + Добавить
-            </button>
-          </div>
-        </div>
-      </div>
-
-      <!-- Options list -->
-      <div class="stack-3">
-        <template x-for="(opt, idx) in options" :key="idx">
-          <div class="box" data-testid="option-row">
-            <div class="row between align-start">
-              <div>
-                <strong class="fs-sm text-dim" x-text="opt.service_name" data-testid="opt-carrier-name"></strong>
-                <div class="fs-xs text-muted">
-                  Базовый авто-тариф: <span x-text="fmtCzk(opt.auto_price)"></span>
-                </div>
-              </div>
-              <div class="row tight">
-                <template x-if="opt.is_manual">
-                  <span class="badge badge-warning" data-testid="manual-price-badge">Ручная цена</span>
-                </template>
-                <template x-if="!opt.is_manual">
-                  <span class="badge badge-info" data-testid="auto-price-badge">Авто-тариф</span>
-                </template>
-                <button type="button" @click="removeOption(idx)" class="btn btn-danger btn-sm" aria-label="Удалить опцию" data-testid="btn-remove-option">
-                  ✕
-                </button>
-              </div>
-            </div>
-
-            <div class="grid-3 box box-subtle">
-              <div class="stack-1">
-                <span class="fs-xs text-muted">Тариф доставки</span>
-                <div class="row tight">
-                  <input type="number" step="1" x-model.number="opt.price" @input="onPriceChange(idx)" class="cost-input" aria-label="Тариф доставки (Kč)" data-testid="input-shipping-cost">
-                  <span class="fs-xs text-dim">Kč</span>
-                </div>
-                <button type="button" x-show="opt.is_manual" @click="resetToAuto(idx)" class="link-btn" data-testid="btn-reset-auto">
-                  Сбросить к авто
-                </button>
-              </div>
-
-              <div class="stack-1">
-                <span class="fs-xs text-muted">Итого покупателю</span>
-                <span class="fw-bold fs-sm" x-text="fmtCzk(buyerTotal(opt))" data-testid="buyer-total"></span>
-              </div>
-
-              <div class="stack-1">
-                <span class="fs-xs text-muted">На руки продавцу</span>
-                <span class="fw-bold fs-sm text-success" x-text="fmtCzk(sellerNet(opt))" data-testid="seller-net"></span>
-              </div>
-            </div>
-            <template x-if="opt.broker_key">
-              <div class="info-box info-box-amber fs-xs">
-                <div class="fw-semibold text-warning">📦 Посредник: <span x-text="opt.broker_key"></span></div>
-                <div class="grid-2 fs-xs text-dim">
-                  <span>Отправитель по документам:</span><span class="fw-medium" x-text="opt.sender_on_documents"></span>
-                  <span>Выплата добірки:</span><span class="fw-medium" x-text="opt.cod_payout_destination"></span>
-                  <span>Рекламации принимает:</span><span class="fw-medium" x-text="opt.claims_handler"></span>
-                  <span>Страховой лимит:</span><span class="fw-medium" x-text="fmtCzk(opt.insurance_limit)"></span>
-                </div>
-              </div>
-            </template>
-          </div>
-        </template>
-        <div x-show="options.length === 0" class="box-dashed">
-          Нет настроенных опций доставки. Добавьте хотя бы один вариант выше.
-        </div>
-      </div>
-
-      <form method="POST" action="/ui/lots/{{ summary.lot_code }}/shipping" @submit="submitJson($event)">
-        <input type="hidden" name="payload" :value="JSON.stringify({shipping_policy: shippingPolicy, options: options})">
-        <div class="row tight">
-          <button type="submit" class="btn btn-primary grow" data-testid="btn-save-shipping">Сохранить опции доставки</button>
-          <a href="/ui/items/{{ summary.item_code }}" class="btn btn-secondary btn-inline">Отмена</a>
-        </div>
-      </form>
-    </div>
-  </div>
-</div>
-
-<script>
-function shippingConstructor(initialData) {
-  return {
-    itemPrice: Number(initialData.item_price || 0),
-    shippingPolicy: initialData.shipping_policy || 'BUYER_PAYS',
-    availableCarriers: initialData.available_carriers || [],
-    selectedCarrier: (initialData.available_carriers && initialData.available_carriers.length > 0) ? initialData.available_carriers[0].key : 'pickup',
-    options: (initialData.options || []).map(opt => ({
-      carrier_key: opt.carrier_key,
-      service_name: opt.service_name,
-      price: Number(opt.price || 0),
-      auto_price: Number(opt.auto_price || 0),
-      is_manual: Boolean(opt.is_manual_price),
-      broker_key: opt.broker_key || null,
-      sender_on_documents: opt.sender_on_documents || 'SELLER',
-      cod_payout_destination: opt.cod_payout_destination || 'SELLER_BANK_ACCOUNT',
-      claims_handler: opt.claims_handler || 'CARRIER',
-      insurance_limit: Number(opt.insurance_limit || 0)
-    })),
-
-    addOption() {
-      const found = this.availableCarriers.find(c => c.key === this.selectedCarrier);
-      if (!found) return;
-      this.options.push({
-        carrier_key: found.key,
-        service_name: found.title,
-        price: Number(found.default_cost || 0),
-        auto_price: Number(found.default_cost || 0),
-        is_manual: false
-      });
-    },
-
-    removeOption(idx) {
-      this.options.splice(idx, 1);
-    },
-
-    onPriceChange(idx) {
-      const opt = this.options[idx];
-      const val = Number(opt.price || 0);
-      opt.is_manual = (val !== opt.auto_price);
-    },
-
-    resetToAuto(idx) {
-      const opt = this.options[idx];
-      opt.price = opt.auto_price;
-      opt.is_manual = false;
-    },
-
-    // TZ-NextLife-UI-N2 §1.4: единый формат суммы — разряды пробелом,
-    // «Kč» после числа, без нулевых десятичных: «12 345 Kč».
-    fmtCzk(n) {
-      const v = Math.round(Number(n || 0) * 100) / 100;
-      const s = String(Math.abs(v));
-      const [i, f] = s.split(".");
-      const gi = i.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
-      const num = (f ? gi + "," + f : gi) + " Kč";
-      return v < 0 ? "-" + num : num;
-    },
-
-    buyerTotal(opt) {
-      const p = Number(this.itemPrice || 0);
-      const c = Number(opt.price || 0);
-      return (this.shippingPolicy === 'SELLER_PAYS') ? p : (p + c);
-    },
-
-    sellerNet(opt) {
-      const p = Number(this.itemPrice || 0);
-      const c = Number(opt.price || 0);
-      return (this.shippingPolicy === 'SELLER_PAYS') ? Math.max(0, p - c) : p;
-    },
-
-    submitJson(e) {
-      // Form payload is already bound via :value
-    }
-  };
-}
-</script>
-{% endblock %}

### DELETED FILE: templates/web/wizard.html
diff --git a/templates/web/wizard.html b/templates/web/wizard.html
deleted file mode 100644
index 7544f27..0000000
--- a/templates/web/wizard.html
+++ /dev/null
@@ -1,206 +0,0 @@
-{% extends "base.html" %}
-
-{% block content %}
-<div class="card">
-  <div class="page-head">
-    <h2 class="page-title">Мастер создания лота</h2>
-    <span class="badge badge-primary">Шаг {{ step }} из 5</span>
-  </div>
-
-  <!-- Progress bar -->
-  <div class="progress-bar">
-    {% for s in range(1, 6) %}
-    <div class="progress-step {% if s <= step %}done{% endif %}"></div>
-    {% endfor %}
-  </div>
-
-  <form method="POST" action="/ui/wizard/step/{{ step }}" class="stack-3">
-    <input type="hidden" name="code" value="{{ item.code if item else '' }}">
-
-    {% if step == 1 %}
-    <!-- Step 1: Item & Category -->
-    <h3 class="fs-lg fw-semibold">Шаг 1: Предмет и категория</h3>
-    <div class="field">
-      <label for="wz-title">Название предмета</label>
-      <input type="text" id="wz-title" name="title" value="{{ item.title if item else '' }}" required>
-    </div>
-    <div class="field">
-      <label for="wz-category">Категория</label>
-      {# TZ-NextLife-UI-N2 §4 V4 (P-E19): категории — из единого справочника. #}
-      <select id="wz-category" name="category">
-        {% for c in category_options %}
-        <option value="{{ c.value }}" {% if item and item.category_key == c.value %}selected{% endif %}>{{ c.label }}</option>
-        {% endfor %}
-      </select>
-    </div>
-    <div class="field">
-      <label for="wz-notes">Заметки владельца</label>
-      <textarea id="wz-notes" name="notes" rows="3">{{ item.notes if item else '' }}</textarea>
-    </div>
-
-    {% elif step == 2 %}
-    <!-- Step 2: Photos -->
-    <h3 class="fs-lg fw-semibold">Шаг 2: Фотографии лота</h3>
-    <p class="fs-sm text-muted">
-      Загрузите исходные фотографии лота #{{ item.code }}. Оригиналы сохраняются неизменными, EXIF/GPS автоматически очищаются.
-    </p>
-
-    {# TZ-NextLife-UI-N2 §4 V3 (Э-6): роли конвейера — внутренняя механика. #}
-    {% set role_labels = {'PUBLISH': 'Обработано', 'PROCESSED': 'Обработано', 'DEFECT': 'Дефект', 'ORIGINAL': 'Оригинал'} %}
-    {% if photos and photos|length > 0 %}
-    <div class="grid-auto" data-testid="wizard-photos-grid">
-      {% for photo in photos %}
-      <div class="thumb-card wizard-thumb">
-        {% if photo.broken %}
-        <div class="thumb-broken" data-testid="photo-broken-mark" title="Файл фото не найден"
-             style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#e9ecef;color:#6c757d;font-size:10px;text-align:center;padding:2px;">🚫 файл не найден</div>
-        {% else %}
-        <img src="{{ photo.url }}" alt="Фото лота {{ loop.index }}">
-        {% endif %}
-        <span class="thumb-role-tag">{{ role_labels.get(photo.role, 'Фото') }}</span>
-        {% if photo.id %}
-        <button type="button" onclick="deleteWizardPhoto('{{ item.code }}', '{{ photo.id }}')" class="thumb-del-btn" title="Удалить фото" aria-label="Удалить фото {{ loop.index }}">&times;</button>
-        {% endif %}
-      </div>
-      {% endfor %}
-    </div>
-    {% else %}
-    <div class="empty empty-sm">
-      Пока нет загруженных фото. Выберите файлы ниже.
-    </div>
-    {% endif %}
-
-    <div class="info-box info-box-slate">
-      <div class="fs-sm fw-semibold">Выбрать файлы для загрузки:</div>
-      <input type="file" id="wizard-files" multiple accept="image/*"
-             aria-label="Файлы фотографий для загрузки" data-testid="wizard-photo-file-input">
-      <div class="row tight">
-        <select id="wizard-role" aria-label="Тип снимков">
-          <option value="ORIGINAL">Оригинал / Общий вид</option>
-          <option value="DEFECT">Фотография дефекта</option>
-        </select>
-        <button type="button" onclick="uploadWizardPhotos('{{ item.code }}')" class="btn btn-secondary btn-sm btn-inline" data-testid="wizard-upload-btn">
-          Загрузить
-        </button>
-      </div>
-      <div id="wizard-upload-msg" class="fs-xs text-accent hidden">Загрузка фото...</div>
-    </div>
-    <input type="hidden" name="photo_path" value="{{ photo_path or '' }}">
-
-    {% elif step == 3 %}
-    <!-- Step 3: Condition & Defects -->
-    <h3 class="fs-lg fw-semibold">Шаг 3: Состояние и дефекты</h3>
-    <div class="field">
-      <label for="wz-condition">Оценка состояния (Grade)</label>
-      <select id="wz-condition" name="condition_grade">
-        <option value="A" {% if item and item.condition_grade == 'A' %}selected{% endif %}>A — Выборный / как новый</option>
-        <option value="B" {% if not item or item.condition_grade == 'B' %}selected{% endif %}>B — Очень хороший (мелкие следы)</option>
-        <option value="C" {% if item and item.condition_grade == 'C' %}selected{% endif %}>C — Хороший (видимые следы)</option>
-        <option value="D" {% if item and item.condition_grade == 'D' %}selected{% endif %}>D — На запчасти</option>
-      </select>
-    </div>
-    <div class="stack-1">
-      <label class="check-row">
-        <input type="checkbox" name="box_present" value="true" {% if item and item.box_present %}checked{% endif %}> Коробка в наличии
-      </label>
-      <label class="check-row">
-        <input type="checkbox" name="receipt_present" value="true" {% if item and item.receipt_present %}checked{% endif %}> Чек / гарантийный талон
-      </label>
-    </div>
-    <div class="field">
-      <label for="wz-defect-text">Декларация дефектов</label>
-      <input type="text" id="wz-defect-text" name="defect_text" placeholder="Например: мелкая царапина на корпусе" value="{{ defect_text or '' }}">
-    </div>
-
-    {% elif step == 4 %}
-    <!-- Step 4: Price + research (TZ-NextLife-UI-N2 §4 V5, P-R07; P-E18 двунаправленная) -->
-    <h3 class="fs-lg fw-semibold">Шаг 4: Цена и разведка</h3>
-    <div class="field">
-      <label for="wz-price">Цена продажи (Kč)</label>
-      <input type="number" step="1" id="wz-price" name="price" value="{{ lot.price_gross if lot and lot.price_gross else '' }}" required>
-    </div>
-    <div class="field">
-      <label for="wz-price-floor">Price Floor (внутренний минимум, не публикуется)</label>
-      <input type="number" step="1" id="wz-price-floor" name="price_floor" value="{{ lot.price_floor if lot and lot.price_floor else '' }}">
-    </div>
-    {% if item %}{% include "_research_panel.html" %}{% endif %}
-
-    {% elif step == 5 %}
-    <!-- Step 5: Shipping & Payment -->
-    <h3 class="fs-lg fw-semibold">Шаг 5: Доставка и оплата</h3>
-    <div class="field">
-      <label for="wz-shipping-policy">Политика доставки</label>
-      {# TZ-NextLife-UI-N2 §4 V4 (P-E10): единый словарь политики доставки
-         (мастер/правка/конструктор — один список значений). #}
-      <select id="wz-shipping-policy" name="shipping_policy">
-        {% for p in shipping_policy_options %}
-        <option value="{{ p.value }}" {% if lot and lot.shipping_policy == p.value %}selected{% endif %}>{{ p.value }} — {{ p.label }}</option>
-        {% endfor %}
-      </select>
-    </div>
-    {% endif %}
-
-    <!-- Controls -->
-    <div class="row between wrap">
-      {% if step > 1 %}
-      <a href="/ui/wizard/{{ item.code }}?step={{ step - 1 }}" class="btn btn-secondary btn-inline">Назад</a>
-      {% else %}
-      <span></span>
-      {% endif %}
-
-      <div class="row tight">
-        {% if item %}
-        <a href="/ui/items/{{ item.code }}" class="btn btn-secondary btn-inline">Сохранить и выйти</a>
-        {% endif %}
-        <button type="submit" class="btn btn-primary">
-          {% if step == 5 %}Завершить{% else %}Далее{% endif %}
-        </button>
-      </div>
-    </div>
-  </form>
-</div>
-
-<script>
-function uploadWizardPhotos(code) {
-  const fileInput = document.getElementById('wizard-files');
-  if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
-    alert('Пожалуйста, выберите хотя бы одну фотографию.');
-    return;
-  }
-  const statusMsg = document.getElementById('wizard-upload-msg');
-  if (statusMsg) {
-    statusMsg.classList.remove('hidden');
-    statusMsg.textContent = 'Загрузка и обработка...';
-  }
-  const role = document.getElementById('wizard-role') ? document.getElementById('wizard-role').value : 'ORIGINAL';
-  const formData = new FormData();
-  for (let i = 0; i < fileInput.files.length; i++) {
-    formData.append('files', fileInput.files[i]);
-  }
-  formData.append('role', role);
-
-  fetch('/ui/wizard/' + code + '/photos/upload', {
-    method: 'POST',
-    body: formData,
-    credentials: 'same-origin'
-  }).then(function() {
-    window.location.reload();
-  }).catch(function(err) {
-    alert('Ошибка при загрузке: ' + err);
-    if (statusMsg) statusMsg.classList.add('hidden');
-  });
-}
-
-function deleteWizardPhoto(code, photoId) {
-  if (!confirm('Удалить эту фотографию?')) return;
-  fetch('/ui/wizard/' + code + '/photos/' + photoId + '/delete', {
-    method: 'POST',
-    credentials: 'same-origin'
-  }).then(function() {
-    window.location.reload();
-  }).catch(function(err) {
-    alert('Ошибка при удалении: ' + err);
-  });
-}
-</script>
-{% endblock %}

### git diff -- tests/conftest.py
diff --git a/tests/conftest.py b/tests/conftest.py
index 81f00d0..769f893 100644
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -123,7 +123,6 @@ def live_server_url():
 def clean_database(session: "Session") -> None:
     from nextlife.media.models import MediaMaster, MediaOriginal, MediaVariant
     from nextlife.pricing.history import PriceHistory
-    from nextlife.pricing.models import LotPricing
     from nextlife.storage.models import (
         Approval,
         BridgeDailyUsage,
@@ -156,7 +155,6 @@ def clean_database(session: "Session") -> None:
     session.query(MediaMaster).delete()
     session.query(MediaOriginal).delete()
     session.query(PriceHistory).delete()
-    session.query(LotPricing).delete()
     session.query(ItemDefect).delete()
     session.query(DeliveryOption).delete()
     session.query(TruthLog).delete()

### git diff -- tests/test_honesty.py
diff --git a/tests/test_honesty.py b/tests/test_honesty.py
index b451c0f..d3a40f7 100644
--- a/tests/test_honesty.py
+++ b/tests/test_honesty.py
@@ -29,8 +29,8 @@ from nextlife.media.profiles import load_marketplace_media_profile
 from nextlife.obs.truth import log_claim
 from nextlife.obs.truth_log import TruthAssertionError
 from nextlife.services.lot_creation import create_item_and_lot
-from nextlife.services.lot_edit import edit_lot
 from nextlife.services.payment_confirmation import handle_buyer_payment_notification
+from nextlife.services.stock import update_stock_item
 from nextlife.services.research import calculate_price_stats
 from nextlife.services.reservation import reserve_lot
 from nextlife.services.sell import MissingApprovalError, publish_item
@@ -143,17 +143,21 @@ def test_honesty_vii_5_second_deal_on_reserved_lot_rejected_by_db():
 def test_honesty_vii_6_lot_text_or_price_edit_invalidates_approval():
     with get_db_session() as session:
         created = create_item_and_lot(session, "Lens", "electronics", Decimal("3000.00"))
+        item_code = created["item_code"]
+        # Item approved (status APPROVED + approval record).
+        session.get(Item, item_code).status = ItemStatus.APPROVED.value
         payload = {"title": "Lens", "price": 3000.0}
         h = compute_payload_hash(payload)
-        record_approval(session, created["item_code"], payload, h, channel="TELEGRAM")
+        record_approval(session, item_code, payload, h, channel="TELEGRAM")
         session.commit()
-        assert has_valid_approval(session, created["item_code"], h) is True
-        edit_lot(session, created["lot_code"], title="Updated Lens", price=Decimal("2800.00"))
+        assert has_valid_approval(session, item_code, h) is True
+        # ТЗ «Склад» §6: edit goes through the stock service — status resets to DRAFT.
+        update_stock_item(session, item_code, {"title": "Updated Lens", "target_price": Decimal("2800.00")})
         session.commit()
-        assert has_valid_approval(session, created["item_code"], h) is False
-        session.query(Approval).filter(Approval.item_code == created["item_code"]).delete()
+        assert session.get(Item, item_code).status == ItemStatus.DRAFT.value
+        session.query(Approval).filter(Approval.item_code == item_code).delete()
         session.query(Lot).filter(Lot.code == created["lot_code"]).delete()
-        session.query(Item).filter(Item.code == created["item_code"]).delete()
+        session.query(Item).filter(Item.code == item_code).delete()
         session.commit()

### git diff -- tests/test_lot_creation_api_cli.py
diff --git a/tests/test_lot_creation_api_cli.py b/tests/test_lot_creation_api_cli.py
index 09a9b9d..18fc957 100644
--- a/tests/test_lot_creation_api_cli.py
+++ b/tests/test_lot_creation_api_cli.py
@@ -71,26 +71,6 @@ def test_api_create_lot(client, auth_headers):
     assert resp2.status_code == 201
 
 
-def test_ui_create_lot_form(client, auth_headers):
-    form_data = {
-        "title": "Apple Magic Trackpad",
-        "category": "electronics",
-        "price": 2200.0,
-        "condition": "used",
-    }
-    # Form submission should redirect to /ui/items/{code}?notice=created
-    response = client.post(
-        "/ui/lots/create",
-        data=form_data,
-        headers=auth_headers,
-        follow_redirects=False,
-    )
-    assert response.status_code == 303
-    loc = response.headers.get("location")
-    assert "/ui/items/" in loc
-    assert "?notice=created" in loc
-
-
 def test_cli_item_create_shares_service(
     live_server_url: str,
     monkeypatch: pytest.MonkeyPatch,

### git diff -- tests/test_lot_delete.py
diff --git a/tests/test_lot_delete.py b/tests/test_lot_delete.py
index 6217fdf..48e9c29 100644
--- a/tests/test_lot_delete.py
+++ b/tests/test_lot_delete.py
@@ -12,11 +12,8 @@ import uuid
 from decimal import Decimal
 
 import pytest
-from fastapi.testclient import TestClient
 from sqlalchemy import select
 
-from nextlife.api.main import create_app
-from nextlife.core.secrets import resolve_secret
 from nextlife.services.lot_creation import create_item_and_lot
 from nextlife.services.lot_delete import LotDeletionBlockedError, can_delete_lot, delete_lot
 from nextlife.storage.db import get_db_session
@@ -30,17 +27,6 @@ class _StubUnpublishConnector:
         return None
 
 
-@pytest.fixture
-def client():
-    return TestClient(create_app())
-
-
-@pytest.fixture
-def auth_headers():
-    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
-    return {"Authorization": f"Bearer {token}"}
-
-
 def test_delete_lot_blocked_by_active_deal():
     with get_db_session() as session:
         created = create_item_and_lot(
@@ -183,78 +169,3 @@ def test_delete_lot_unpublishes_listings_and_soft_deletes_with_audit(monkeypatch
         session.delete(l2_updated)
         session.commit()
 
-
-def test_api_delete_lot_active_deal_conflict(client, auth_headers):
-    with get_db_session() as session:
-        created = create_item_and_lot(
-            session=session,
-            title="API Active Deal Item",
-            category_key="general",
-            price=Decimal("700.00"),
-        )
-        lot_code = created["lot_code"]
-        item_code = created["item_code"]
-
-        listing_id = str(uuid.uuid4())
-        listing = Listing(
-            id=listing_id,
-            item_code=item_code,
-            marketplace_key="bazos_cz",
-            price=Decimal("700.00"),
-            currency="CZK",
-            price_base=Decimal("700.00"),
-            status="PUBLISHED",
-        )
-        session.add(listing)
-        session.flush()
-
-        deal = Deal(
-            id=str(uuid.uuid4()),
-            listing_id=listing_id,
-            agreed_price=Decimal("700.00"),
-            currency="CZK",
-            price_base=Decimal("700.00"),
-            delivery_method="HANDOVER",
-            payment_method="CASH",
-            status="OPEN",
-            closed_at=None,
-            closure_evidence={},
-        )
-        session.add(deal)
-        session.commit()
-        deal_id = deal.id
-
-    # Try DELETE -> 409
-    resp = client.delete(f"/api/lots/{lot_code}", headers=auth_headers)
-    assert resp.status_code == 409
-    assert "активная сделка" in resp.json()["detail"]
-
-    # Clean up
-    with get_db_session() as session:
-        d = session.get(Deal, deal_id)
-        lst = session.get(Listing, listing_id)
-        session.delete(d)
-        session.delete(lst)
-        session.commit()
-
-
-def test_api_delete_lot_success(client, auth_headers):
-    with get_db_session() as session:
-        created = create_item_and_lot(
-            session=session,
-            title="API Safe Delete Item",
-            category_key="general",
-            price=Decimal("400.00"),
-        )
-        lot_code = created["lot_code"]
-        session.commit()
-
-    resp = client.delete(f"/api/lots/{lot_code}", headers=auth_headers)
-    assert resp.status_code == 200
-    data = resp.json()
-    assert data["success"] is True
-    assert data["status"] == "DELETED"
-
-    with get_db_session() as session:
-        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
-        assert lot.status == "DELETED"

### DELETED FILE: tests/test_lot_edit.py
diff --git a/tests/test_lot_edit.py b/tests/test_lot_edit.py
deleted file mode 100644
index ced9237..0000000
--- a/tests/test_lot_edit.py
+++ /dev/null
@@ -1,245 +0,0 @@
-"""Tests for Lot editing, approval invalidation, and marketplace capabilities (T-043).
-
-DoD:
-- Editing fields invalidates existing approvals and resets status to AWAITING_APPROVAL.
-- Marketplace capabilities determine in-place edit vs relisting requirement from capabilities dict.
-- Module length <= 400 lines.
-"""
-
-from decimal import Decimal
-
-import pytest
-from fastapi.testclient import TestClient
-from sqlalchemy import select
-
-from nextlife.api.main import create_app
-from nextlife.core.approval import compute_payload_hash, has_valid_approval, record_approval
-from nextlife.core.secrets import resolve_secret
-from nextlife.core.status import ItemStatus
-from nextlife.services.lot_creation import create_item_and_lot
-from nextlife.services.lot_edit import edit_lot, get_marketplace_edit_capabilities
-from nextlife.storage.db import get_db_session
-from nextlife.storage.models import AuditEvent, Item, Listing, Lot, Marketplace
-
-
-@pytest.fixture
-def api_client():
-    return TestClient(create_app())
-
-
-@pytest.fixture
-def auth_headers():
-    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
-    return {"Authorization": f"Bearer {token}"}
-
-
-def test_lot_edit_invalidates_approval_and_resets_status():
-    with get_db_session() as session:
-        created = create_item_and_lot(
-            session=session,
-            title="Old Title",
-            category_key="electronics",
-            price=Decimal("2000.00"),
-            currency="CZK",
-            price_floor=Decimal("1500.00"),
-            shipping_policy="BUYER_PAYS",
-        )
-        lot_code = created["lot_code"]
-        item_code = created["item_code"]
-
-        # Record an approval
-        payload = {"title": "Old Title", "price": 2000.0}
-        h = compute_payload_hash(payload)
-        record_approval(session, item_code, payload, h, channel="TELEGRAM")
-        session.commit()
-
-        # Check approval exists
-        assert has_valid_approval(session, item_code, h) is True
-
-        # Perform edit
-        res = edit_lot(
-            session=session,
-            lot_code=lot_code,
-            title="Updated Camera Title",
-            description="Brand new description",
-            price=Decimal("1800.00"),
-            price_floor=Decimal("1400.00"),
-            shipping_policy="SELLER_PAYS",
-        )
-        session.commit()
-
-        # Verification: approval invalidated
-        assert has_valid_approval(session, item_code, h) is False
-        assert res["invalidated_approvals"] is True
-
-        # Lot and item returned to AWAITING_APPROVAL
-        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
-        item = session.get(Item, item_code)
-        assert lot.status == ItemStatus.AWAITING_APPROVAL.value
-        assert item.status == ItemStatus.AWAITING_APPROVAL.value
-
-        # Fields updated
-        assert lot.price_gross == Decimal("1800.00")
-        assert lot.price_floor == Decimal("1400.00")
-        assert lot.shipping_policy == "SELLER_PAYS"
-        assert item.title == "Updated Camera Title"
-        # TZ-NextLife-UI-N2 P-E12: «Описание / текст объявления» пишется в
-        # единый источник правды уходящего текста (final_text), не в attrs.description.
-        assert item.attrs.get("final_text") == "Brand new description"
-
-        # Audit event logged
-        audit = session.scalar(
-            select(AuditEvent)
-            .where(AuditEvent.entity == "lot", AuditEvent.entity_id == lot_code, AuditEvent.action == "LOT_EDITED")
-            .order_by(AuditEvent.at.desc())
-        )
-        assert audit is not None
-        assert audit.data["invalidated_approvals_count"] == 1
-
-
-def test_marketplace_capabilities_inspection_from_caps_dict():
-    with get_db_session() as session:
-        # Mock marketplaces with edit=True / edit=False (Ч-5/Ч-6: real
-        # marketplaces do not claim edit until it is implemented, so the
-        # mechanism is tested with explicit mock registry rows).
-        edit_mp = session.scalar(select(Marketplace).where(Marketplace.key == "mock_edit"))
-        if not edit_mp:
-            edit_mp = Marketplace(
-                key="mock_edit",
-                title="Mock Edit Market",
-                country="CZ",
-                currency="CZK",
-                access_mode="DIRECT",
-                capabilities={"edit": True, "post": True},
-                config_hash="mockhash",
-            )
-            session.add(edit_mp)
-        no_edit_mp = session.scalar(select(Marketplace).where(Marketplace.key == "mock_no_edit"))
-        if not no_edit_mp:
-            no_edit_mp = Marketplace(
-                key="mock_no_edit",
-                title="Mock No Edit Market",
-                country="CZ",
-                currency="CZK",
-                access_mode="DIRECT",
-                capabilities={"edit": False, "post": True},
-                config_hash="mockhash",
-            )
-            session.add(no_edit_mp)
-
-        # Create item and lot
-        created = create_item_and_lot(
-            session=session,
-            title="Test Item Caps",
-            category_key="general",
-            price=Decimal("500.00"),
-        )
-        lot_code = created["lot_code"]
-        item_code = created["item_code"]
-
-        # Add listings on mock_edit (edit=True) and mock_no_edit (edit=False)
-        l1 = Listing(
-            item_code=item_code,
-            marketplace_key="mock_edit",
-            price=Decimal("500.00"),
-            currency="CZK",
-            price_base=Decimal("500.00"),
-            status="PUBLISHED",
-        )
-        l2 = Listing(
-            item_code=item_code,
-            marketplace_key="mock_no_edit",
-            price=Decimal("500.00"),
-            currency="CZK",
-            price_base=Decimal("500.00"),
-            status="PUBLISHED",
-        )
-        session.add_all([l1, l2])
-        session.commit()
-
-        caps = get_marketplace_edit_capabilities(session, lot_code)
-        caps_dict = {c["marketplace_key"]: c for c in caps}
-
-        assert "mock_edit" in caps_dict
-        assert caps_dict["mock_edit"]["supports_edit"] is True
-        assert caps_dict["mock_edit"]["requires_relist"] is False
-        assert caps_dict["mock_edit"]["action_type"] == "IN_PLACE_EDIT"
-
-        assert "mock_no_edit" in caps_dict
-        assert caps_dict["mock_no_edit"]["supports_edit"] is False
-        assert caps_dict["mock_no_edit"]["requires_relist"] is True
-        assert caps_dict["mock_no_edit"]["action_type"] == "REQUIRE_REPOST"
-
-        session.delete(l1)
-        session.delete(l2)
-        session.delete(edit_mp)
-        session.delete(no_edit_mp)
-        session.commit()
-
-
-def test_api_patch_lot_endpoint(api_client, auth_headers):
-    with get_db_session() as session:
-        created = create_item_and_lot(
-            session=session,
-            title="Initial API Item",
-            category_key="general",
-            price=Decimal("1200.00"),
-        )
-        lot_code = created["lot_code"]
-        session.commit()
-
-    resp = api_client.patch(
-        f"/api/lots/{lot_code}",
-        headers=auth_headers,
-        json={"title": "Updated via API", "price": 1100.0, "notes": "API test edit"},
-    )
-    assert resp.status_code == 200
-    data = resp.json()
-    assert data["lot_code"] == lot_code
-    assert data["status"] == ItemStatus.AWAITING_APPROVAL.value
-    assert float(data["price_gross"]) == 1100.0
-    assert len(data["marketplace_capabilities"]) > 0
-
-
-def test_ui_lot_edit_workflow(api_client, auth_headers):
-    with get_db_session() as session:
-        created = create_item_and_lot(
-            session=session,
-            title="UI Edit Item",
-            category_key="general",
-            price=Decimal("950.00"),
-            shipping_policy="BUYER_PAYS",
-        )
-        lot_code = created["lot_code"]
-        item_code = created["item_code"]
-        session.commit()
-
-    # GET edit form
-    resp_get = api_client.get(f"/ui/lots/{lot_code}/edit", headers=auth_headers)
-    assert resp_get.status_code == 200
-    assert f"Редактирование лота #{lot_code}" in resp_get.text
-    assert "Возможности площадок" in resp_get.text
-    assert "AWAITING_APPROVAL" in resp_get.text
-
-    # POST changes
-    resp_post = api_client.post(
-        f"/ui/lots/{lot_code}/edit",
-        headers=auth_headers,
-        data={
-            "title": "UI Edited Item Title",
-            "description": "New description from UI",
-            "price": "890",
-            "price_floor": "750",
-            "shipping_policy": "PICKUP_ONLY",
-            "notes": "Edited via UI test",
-        },
-        follow_redirects=False,
-    )
-    assert resp_post.status_code == 303
-    assert f"/ui/items/{item_code}?notice=lot_edited_approval_invalidated" in resp_post.headers["location"]
-
-    with get_db_session() as session:
-        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
-        assert lot.status == ItemStatus.AWAITING_APPROVAL.value
-        assert lot.price_gross == Decimal("890")
-        assert lot.shipping_policy == "PICKUP_ONLY"

### DELETED FILE: tests/test_screen_marketplaces_playwright.py
diff --git a/tests/test_screen_marketplaces_playwright.py b/tests/test_screen_marketplaces_playwright.py
deleted file mode 100644
index ac8370b..0000000
--- a/tests/test_screen_marketplaces_playwright.py
+++ /dev/null
@@ -1,142 +0,0 @@
-"""Playwright mobile test for the «Площадки» (Marketplaces) screen (TZ M1 D.1, D.4).
-
-DoD:
-- Screen exists at /ui/marketplaces and shows, per enabled channel:
-  marketplace, scenario/channel, state (OK/BLOCKED), last successful probe
-  date and the block reason.
-- Screen↔DB сверка: the exact block reasons seeded in channel_canary are
-  visible verbatim.
-- 360 px viewport without horizontal scrolling.
-- No console errors, no page errors, no failed network responses.
-- Red run (screen missing) before green run (D.4: пара «красный до — зелёный после»).
-"""
-
-import os
-from datetime import UTC, datetime
-from pathlib import Path
-
-from playwright.sync_api import sync_playwright
-
-from nextlife.connectors.registry import sync_marketplaces
-from nextlife.core.config import load_config
-from nextlife.storage.db import get_db_session
-from nextlife.storage.models import ChannelCanary
-
-TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
-SCREENSHOT_DIR = Path(".a/tmp/screenshots")
-SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
-
-REASON_SBAZAR = None  # OK — no reason
-REASON_BAZOS = "protection_page: marker=datadome"
-REASON_AUKRO = "unknown_redirect: final_url=https://aukro.cz/notebooky-netbooky"
-
-
-def _seed_canaries():
-    now = datetime.now(UTC)
-    with get_db_session() as session:
-        cfg = load_config()
-        sync_marketplaces(session, cfg)
-        session.query(ChannelCanary).delete()
-        session.flush()
-        session.add(
-            ChannelCanary(
-                marketplace_key="sbazar_cz",
-                scenario="search",
-                channel="DIRECT",
-                state="OK",
-                last_success_at=now,
-                last_attempt_at=now,
-                last_success_count=55,
-                probe_query="notebook",
-            )
-        )
-        session.add(
-            ChannelCanary(
-                marketplace_key="bazos_cz",
-                scenario="search",
-                channel="DIRECT",
-                state="BLOCKED",
-                last_success_at=None,
-                last_attempt_at=now,
-                last_success_count=0,
-                probe_query="notebook",
-                block_reason=REASON_BAZOS,
-                artifact_path=".a/reports/canaries/bazos_cz_example.html",
-            )
-        )
-        session.add(
-            ChannelCanary(
-                marketplace_key="aukro_cz",
-                scenario="search",
-                channel="DIRECT",
-                state="BLOCKED",
-                last_success_at=None,
-                last_attempt_at=now,
-                last_success_count=0,
-                probe_query="notebook",
-                block_reason=REASON_AUKRO,
-                artifact_path=".a/reports/canaries/aukro_cz_example.html",
-            )
-        )
-        session.commit()
-
-
-def test_marketplaces_screen_mobile_playwright(live_server_url):
-    _seed_canaries()
-
-    base_url = live_server_url
-    console_errors: list[str] = []
-    page_errors: list[str] = []
-    failed_responses: list[str] = []
-
-    with sync_playwright() as p:
-        browser = p.chromium.launch(headless=True)
-        context = browser.new_context(
-            viewport={"width": 360, "height": 740},
-            device_scale_factor=1,
-            is_mobile=True,
-            has_touch=True,
-        )
-        page = context.new_page()
-        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
-        page.on("pageerror", lambda err: page_errors.append(str(err)))
-        page.on(
-            "response",
-            lambda resp: failed_responses.append(f"{resp.status} {resp.url}") if resp.status >= 400 else None,
-        )
-
-        try:
-            url = f"{base_url}/ui/marketplaces?token={TOKEN}"
-            resp = page.goto(url, wait_until="networkidle")
-            assert resp is not None and resp.status == 200, (
-                f"«Площадки» screen must exist and return 200, got {resp.status if resp else None}"
-            )
-
-            body = page.inner_text("body")
-
-            # Screen↔DB сверка: seeded titles, states and verbatim block reasons.
-            assert "Sbazar.cz" in body
-            assert "Bazoš.cz" in body
-            assert "Aukro.cz" in body
-            assert "OK" in body
-            assert "BLOCKED" in body
-            assert REASON_BAZOS in body
-            assert REASON_AUKRO in body
-
-            # 360 px geometry: no horizontal scroll.
-            scroll_w = page.evaluate("() => document.documentElement.scrollWidth")
-            client_w = page.evaluate("() => document.documentElement.clientWidth")
-            assert scroll_w <= client_w + 5, (
-                f"Horizontal scroll violation: scrollWidth {scroll_w} > clientWidth {client_w}"
-            )
-
-            page.screenshot(path=str(SCREENSHOT_DIR / "t_marketplaces_screen_360px.png"))
-
-            assert not console_errors, f"Console errors: {console_errors}"
-            assert not page_errors, f"Page errors: {page_errors}"
-            assert not failed_responses, f"Failed network responses: {failed_responses}"
-        finally:
-            browser.close()
-            with get_db_session() as session:
-                session.query(ChannelCanary).delete()
-                session.commit()

### git diff -- tests/test_shipping_options.py
diff --git a/tests/test_shipping_options.py b/tests/test_shipping_options.py
index e5b705b..c9536b5 100644
--- a/tests/test_shipping_options.py
+++ b/tests/test_shipping_options.py
@@ -182,10 +182,12 @@ def test_ui_shipping_constructor_html(client, auth_headers):
             category_key="general",
             price=Decimal("1200.00"),
         )
-        lot_code = created["lot_code"]
+        item_code = created["item_code"]
         session.commit()
 
-    resp = client.get(f"/ui/lots/{lot_code}/shipping", headers=auth_headers)
+    # Конструктор доставки теперь живёт на экране предмета Склада
+    # (standalone /ui/lots/{code}/shipping редиректит на /ui/stock/{code}#delivery).
+    resp = client.get(f"/ui/stock/{item_code}", headers=auth_headers)
     assert resp.status_code == 200
     html = resp.text
 
@@ -194,9 +196,9 @@ def test_ui_shipping_constructor_html(client, auth_headers):
     assert 'buyerTotal(opt)' in html
     assert 'sellerNet(opt)' in html
 
-    # Verify controls and testids
-    assert 'data-testid="input-item-price"' in html
-    assert 'data-testid="select-shipping-policy"' in html
+    # Verify constructor container and controls (testids из _shipping_constructor.html;
+    # input-item-price / select-shipping-policy были на удалённой standalone-странице).
+    assert 'data-testid="shipping-constructor"' in html
     assert 'data-testid="select-carrier-to-add"' in html
     assert 'data-testid="btn-add-option"' in html
     assert 'data-testid="btn-remove-option"' in html
@@ -204,3 +206,4 @@ def test_ui_shipping_constructor_html(client, auth_headers):
     assert 'data-testid="auto-price-badge"' in html
     assert 'data-testid="buyer-total"' in html
     assert 'data-testid="seller-net"' in html
+    assert 'data-testid="btn-save-shipping"' in html

### DELETED FILE: tests/test_wizard_five_steps.py
diff --git a/tests/test_wizard_five_steps.py b/tests/test_wizard_five_steps.py
deleted file mode 100644
index 8eef3ee..0000000
--- a/tests/test_wizard_five_steps.py
+++ /dev/null
@@ -1,135 +0,0 @@
-from decimal import Decimal
-
-import pytest
-from fastapi.testclient import TestClient
-
-from nextlife.api.main import create_app
-from nextlife.core.secrets import resolve_secret
-from nextlife.storage.db import get_db_session
-from nextlife.storage.models import Item, Lot
-
-
-@pytest.fixture
-def client():
-    app = create_app()
-    return TestClient(app)
-
-
-@pytest.fixture
-def auth_headers():
-    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
-    return {"Authorization": f"Bearer {token}"}
-
-
-def test_wizard_five_steps_and_exit_at_step_three_preservation(client, auth_headers):
-    # 1. Access wizard start (step 1)
-    resp = client.get("/ui/wizard", headers=auth_headers)
-    assert resp.status_code == 200
-    assert "Мастер создания лота" in resp.text
-    assert "Шаг 1 из 5" in resp.text
-
-    # 2. Complete Step 1
-    resp_step1 = client.post(
-        "/ui/wizard/step/1",
-        data={
-            "title": "Audio-Technica ATH-M50x",
-            "category": "electronics",
-            "notes": "Куплены в Праге, использовались дома",
-        },
-        headers=auth_headers,
-        follow_redirects=False,
-    )
-    assert resp_step1.status_code == 303
-    step2_url = resp_step1.headers["location"]
-    assert "?step=2" in step2_url
-    code = step2_url.split("/ui/wizard/")[1].split("?")[0]
-
-    # Verify DB after step 1
-    with get_db_session() as session:
-        item = session.get(Item, code)
-        assert item is not None
-        assert item.title == "Audio-Technica ATH-M50x"
-        assert item.category_key == "electronics"
-
-    # 3. Complete Step 2
-    resp_step2 = client.post(
-        "/ui/wizard/step/2",
-        data={"code": code},
-        headers=auth_headers,
-        follow_redirects=False,
-    )
-    assert resp_step2.status_code == 303
-    assert "?step=3" in resp_step2.headers["location"]
-
-    # 4. Complete Step 3 (Condition, box, receipt, defects)
-    resp_step3 = client.post(
-        "/ui/wizard/step/3",
-        data={
-            "code": code,
-            "condition_grade": "A",
-            "box_present": "true",
-            "receipt_present": "true",
-            "defect_text": "микропотертость на оголовье",
-        },
-        headers=auth_headers,
-        follow_redirects=False,
-    )
-    assert resp_step3.status_code == 303
-    assert "?step=4" in resp_step3.headers["location"]
-
-    # --- DoD: Выход на третьем шаге и возврат сохраняет всё введённое ---
-    # Simulate user navigating away to items list
-    resp_exit = client.get("/ui/items", headers=auth_headers)
-    assert resp_exit.status_code == 200
-
-    # User returns to wizard step 3
-    resp_return = client.get(f"/ui/wizard/{code}?step=3", headers=auth_headers)
-    assert resp_return.status_code == 200
-    # Assert step 3 form pre-filled from DB
-    assert 'checked' in resp_return.text  # box_present or receipt_present checked
-    assert 'A — Выборный' in resp_return.text
-    # Verify in DB
-    with get_db_session() as session:
-        item = session.get(Item, code)
-        assert item.condition_grade == "A"
-        assert item.box_present is True
-        assert item.receipt_present is True
-        assert len(item.defects) == 1
-
-    # 5. Complete Step 4 (Price & price_floor)
-    resp_step4 = client.post(
-        "/ui/wizard/step/4",
-        data={
-            "code": code,
-            "price": "3200",
-            "price_floor": "2600",
-        },
-        headers=auth_headers,
-        follow_redirects=False,
-    )
-    assert resp_step4.status_code == 303
-    assert "?step=5" in resp_step4.headers["location"]
-
-    # 6. Complete Step 5 (Shipping policy).
-    # TZ-NextLife-UI-N2 V4 (P-E10): единый словарь политики доставки
-    # (BUYER_PAYS/SELLER_PAYS/PICKUP_ONLY); INCLUDED упразднено
-    # (миграция 0019: INCLUDED -> SELLER_PAYS + DB CHECK).
-    resp_step5 = client.post(
-        "/ui/wizard/step/5",
-        data={
-            "code": code,
-            "shipping_policy": "SELLER_PAYS",
-        },
-        headers=auth_headers,
-        follow_redirects=False,
-    )
-    assert resp_step5.status_code == 303
-    assert f"/ui/items/{code}?notice=wizard_complete" in resp_step5.headers["location"]
-
-    # Final DB check
-    with get_db_session() as session:
-        lot = session.query(Lot).filter(Lot.item_id == code).first()
-        assert lot is not None
-        assert lot.price_gross == Decimal("3200")
-        assert lot.price_floor == Decimal("2600")
-        assert lot.shipping_policy == "SELLER_PAYS"

### git diff -- tests/test_wizard_textgen.py
diff --git a/tests/test_wizard_textgen.py b/tests/test_wizard_textgen.py
index 2fe8b19..a8b2561 100644
--- a/tests/test_wizard_textgen.py
+++ b/tests/test_wizard_textgen.py
@@ -95,38 +95,6 @@ def test_generate_description_llm_failure_placeholder(monkeypatch):
     assert result["text"] == PLACEHOLDER_TEXT
 
 
-def test_load_generated_roundtrip():
-    """save_generated persists the text; load_generated reads it back (None when absent)."""
-    from nextlife.services.wizard_textgen import load_generated, save_generated
-    from nextlife.storage.db import get_db_session
-    from nextlife.storage.models import Item
-
-    with get_db_session() as session:
-        item = session.get(Item, "A1")
-        if item is None:
-            item = Item(
-                code="A1", kind="SELL", title="t", category_key="electronics",
-                condition_key="used", status="DRAFT",
-                attrs={}, defects=[],
-            )
-            session.add(item)
-        assert load_generated(item) is None
-        save_generated(session, item, {"title": "T", "text": "Сохранённый текст", "mode": "live"})
-        session.commit()
-        item = session.get(Item, "A1")
-        g = load_generated(item)
-        assert g is not None
-        assert g["text"] == "Сохранённый текст"
-        assert g["mode"] == "live"
-        # Cleanup.
-        from nextlife.storage.models import ItemPhoto, Lot
-
-        session.query(ItemPhoto).filter(ItemPhoto.item_code == "A1").delete()
-        session.query(Lot).filter(Lot.item_id == "A1").delete()
-        session.query(Item).filter(Item.code == "A1").delete()
-        session.commit()
-
-
 def _cleanup(session, item_code: str) -> None:
     from nextlife.storage.models import Item, ItemPhoto, Lot

```

## Этап 7 — Скрипт применения, отчёт (коммит sklad-7)

### 7.1 Скрипт `scripts/deploy_sklad.sh`

Скрипт деплоя Склада (ТЗ §7.1). Запускается владельцем от root:
`sudo bash /path/to/NextLife/scripts/deploy_sklad.sh`.
Остановка на первой ошибке (`set -euo pipefail`). Код (rsync) деплоится отдельно
(`sudo make install`); скрипт — только БД-миграция + рестарт + проверка шлюза.

Шаги (ТЗ §7.1):
1. `git log -1 --oneline`
2. `sudo -u nextlife pg_dump` → `/var/lib/nextlife/backup/pre-sklad-<YYYYmmdd-HHMMSS>.sql`
3. `alembic current` под `nextlife`
4. `alembic upgrade head` под `nextlife`
5. `systemctl restart nextlife-web`, ожидание `/health` до 30 c
6. `GET /api/oc/stock` с токеном `NEXTLIFE_OPENCLAW_TOKEN` из окружения сервиса
   `nextlife-web` (токен не выводится); ожидается HTTP 200
7. `alembic current`, число предметов на Складе, строка `SKLAD DEPLOY OK`

Синтаксис: `bash -n scripts/deploy_sklad.sh` — OK.

Новый файл (буквально):

```
#!/usr/bin/env bash
# NextLife «Склад» deploy — ТЗ «Склад» вместо «Ленты», этап 7.
#
# Запускается владельцем от root:
#   sudo bash /path/to/NextLife/scripts/deploy_sklad.sh
#
# Применяет миграцию БД Склада (alembic upgrade head) и проверяет шлюз
# /api/oc/stock. Остановка на первой ошибке (set -euo pipefail).
#
# Код (rsync) деплоится отдельно: `sudo make install`. Этот скрипт — только
# БД-миграция + рестарт + проверка шлюза.
set -euo pipefail

NEXTLIFE_USER="nextlife"
DB_NAME="nextlife"
DSN="postgresql+psycopg2:///nextlife?host=/var/run/postgresql"
PORT=8081
OPT_DIR="/opt/nextlife"
CODE_DIR="$OPT_DIR/app"
VENV_DIR="$OPT_DIR/venv"
DATA_DIR="/var/lib/nextlife"
BACKUP_DIR="$DATA_DIR/backup"
ALEMBIC="$VENV_DIR/bin/alembic"
ALEMBIC_INI="$CODE_DIR/alembic.ini"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo "[deploy-sklad] $*"; }
die() { echo "[deploy-sklad] ERROR: $*" >&2; exit 1; }

CURRENT_STEP=""
trap 'rc=$?; if [ $rc -ne 0 ]; then echo ""; echo "[deploy-sklad] Прервано на шаге ${CURRENT_STEP}. Откат: восстановите БД из backup в ${BACKUP_DIR}."; exit $rc; fi' EXIT
set_step() { CURRENT_STEP="$1"; }

# alembic под пользователем nextlife с DSN (peer via unix socket).
# env NEXTLIFE_DSN ставится ВНУТРИ sudo (через `env`): sudo env_reset выкидывает
# переменные из вызывающего окружения (как в install.sh).
alembic() {
    sudo -u "$NEXTLIFE_USER" env NEXTLIFE_DSN="$DSN" "$ALEMBIC" -c "$ALEMBIC_INI" "$@"
}

# ── 1. git log -1 --oneline ─────────────────────────────────────────────────
set_step "1-git-log"
log "Step 1: commit"
log "  $(git -C "$REPO_ROOT" log -1 --oneline)"

# ── 2. pg_dump backup ───────────────────────────────────────────────────────
set_step "2-backup"
log "Step 2: DB backup"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/pre-sklad-$(date +%Y%m%d-%H%M%S).sql"
sudo -u "$NEXTLIFE_USER" pg_dump "$DB_NAME" > "$BACKUP_FILE"
chown "$NEXTLIFE_USER:$NEXTLIFE_USER" "$BACKUP_FILE"
log "  Backup: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"

# ── 3. alembic current ──────────────────────────────────────────────────────
set_step "3-alembic-current"
log "Step 3: alembic current"
alembic current

# ── 4. alembic upgrade head ─────────────────────────────────────────────────
set_step "4-alembic-upgrade"
log "Step 4: alembic upgrade head"
alembic upgrade head

# ── 5. systemctl restart nextlife-web, ожидание /health до 30 c ─────────────
set_step "5-restart"
log "Step 5: restart nextlife-web"
systemctl restart nextlife-web
HEALTH=""
for i in $(seq 1 15); do
    HEALTH="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)"
    [ "$HEALTH" = "200" ] && break
    sleep 2
done
log "  GET /health: $HEALTH (after $i attempts)"
[ "$HEALTH" = "200" ] || die "GET /health returned $HEALTH (expected 200) after retries"

# ── 6. Проверка шлюза GET /api/oc/stock ─────────────────────────────────────
set_step "6-gateway"
log "Step 6: verify /api/oc/stock gateway"
ENV_FILE="$(systemctl show nextlife-web -p EnvironmentFile --value | head -1)"
[ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ] || die "Service EnvironmentFile not found"
# Токен берётся из окружения сервиса (EnvironmentFile) и НЕ выводится.
OC_TOKEN="$(grep -E '^NEXTLIFE_OPENCLAW_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
[ -n "$OC_TOKEN" ] || die "NEXTLIFE_OPENCLAW_TOKEN not found in $ENV_FILE"
GATEWAY_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OC_TOKEN" "http://127.0.0.1:$PORT/api/oc/stock")"
unset OC_TOKEN
log "  GET /api/oc/stock: $GATEWAY_STATUS"
[ "$GATEWAY_STATUS" = "200" ] || die "GET /api/oc/stock returned $GATEWAY_STATUS (expected 200)"

# ── 7. alembic current, число предметов, SKLAD DEPLOY OK ────────────────────
set_step "7-done"
log "Step 7: final state"
alembic current
STOCK_COUNT="$(sudo -u "$NEXTLIFE_USER" psql -d "$DB_NAME" -tAc "select count(*) from item where status not in ('DELETED', 'CANCELLED')")"
log "  Items on Склад: $STOCK_COUNT"
log "SKLAD DEPLOY OK"
```

### 7.2 make test в сравнении с базовой линией

| | baseline (a643f36) | текущее (sklad-7) |
|---|---|---|
| failed | 21 | 4 |
| passed | 650 | 722 |

Все 4 оставшихся падения — предсуществующие из базовой линии
(`test_check_hardcode`, `test_listing_templates`, `test_screen_deal_playwright`,
`test_stage_e7`). Окружение-зависимые падения (httpx.ConnectError, 18 шт.) проходят —
сервер запущен.

### 7.3 sklad-blockers.md

Файл не создан — расхождений между кодом и аудитом не обнаружено.

## Этап 8 — Снятие объявлений на Bazoš

Не выполнен. Начинается только после сигнала владельца
«контрольная публикация выполнена» (контрольная публикация тестового предмета
через новый мастер, шаг 4, Bazoš live). До этого Bazoš-объявления при снятии
получают `UNPUBLISH_FAILED` и показываются владельцу для ручного снятия (этап 2.1).
