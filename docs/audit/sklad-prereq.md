# Аудит: превращение «Ленты» в справочник «Склад»

Формат: **Факт — Источник**. Источник = `file:line`, имя таблицы/колонки или вывод команды.
Механизм отсутствует → «НЕ РЕАЛИЗОВАНО». Есть stub/mock/TODO → «ЗАГЛУШКА» (с местом).
Реализовано, но не запускалось на реальной площадке → «НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ».
Только текущее фактическое состояние. Без предложений и планов.

---

## 1. Модель данных

**Реальный DSN (без пароля):** `postgresql+psycopg2:///nextlife?host=/var/run/postgresql` — peer-аутентификация через unix-socket, OS-пользователь `nextlife`, без пароля. — Источник: `systemctl cat nextlife-web` → `Environment=NEXTLIFE_DSN=postgresql+psycopg2:///nextlife?host=/var/run/postgresql`.

> Примечание: полный `pg_dump --schema-only` в момент аудита недоступен агенту (peer-auth требует OS-пользователя `nextlife`, у агента нет sudo). DDL ниже восстановлен из цепочки alembic-миграций `alembic/versions/0001…0021` — это именно тот SQL, которым создан production-DB (не ORM-модели). `alembic/versions/` содержит 21 ревизию (0001–0021). — Источник: `ls alembic/versions/`.

### Таблицы (реальный DDL из миграций)

- `item` (0001): `code` String(3) PK, `kind` String(10), `title` String(255), `category_key` String(50), `attrs` JSONB, `condition_key` String(50), `defects` JSONB, `status` String(50), `target_price` Numeric(12,2), `target_currency` String(3), `notes` Text, `created_at`, `updated_at`. — Источник: `alembic/versions/20260907_0001_initial_schema.py:22-37`.
- `item_photo` (0001): `id` String(36) PK, `item_code` String(3) FK→item.code, `role` String(20), `path` String(1024), `sha256` String(64), `width` Int, `height` Int, `derived_from_id` String(36) self-FK, `transform_log` JSONB, `ai_generated` Bool, `vlm_verdict` JSONB, `created_at`, `updated_at`. — Источник: `alembic/versions/20260907_0001_initial_schema.py:40-60`.
- `marketplace` (0001): `key` String(50) PK, `title` String(100), `country` String(2), `currency` String(3), `access_mode` String(20), `capabilities` JSONB, `enabled` Bool, `config_hash` String(64). — Источник: `alembic/versions/20260907_0001_initial_schema.py:63-74`.
- `research_run` (0001): `id` PK, `item_code` FK, `started_at`, `finished_at`, `status` String(30), `marketplaces` JSONB, `error` JSONB. — Источник: `alembic/versions/20260907_0001_initial_schema.py:77-88`.
- `research_result` (0001): `id` PK, `run_id` FK, `marketplace_key` FK, `external_url` String(1024), `title` String(255), `price` Numeric(12,2), `currency` String(3), `price_base` Numeric(12,2), `fx_rate_date` Date, `condition_text` String(100), `posted_at`, `raw` JSONB, `captured_at`. — Источник: `alembic/versions/20260907_0001_initial_schema.py:91-108`.
- `listing` (0001 + 0010): `id` PK, `item_code` FK, `marketplace_key` FK, `external_id` String(255), `external_url` String(1024), `price` Numeric(12,2), `currency` String(3), `price_base` Numeric(12,2), `status` String(50), `published_at`, `archived_at`, `last_seen_at`, `publish_evidence` JSONB, `previous_listing_id` (0010). — Источник: `alembic/versions/20260907_0001_initial_schema.py:111-127`, `alembic/versions/20260908_0010_add_listing_previous_listing_id.py`.
- `thread` (0001): `id` PK, `listing_id` FK (nullable с 0015), `channel` String(20), `counterparty` String(255), `lang` String(10), `status` String(30). — Источник: `alembic/versions/20260907_0001_initial_schema.py:130-140`, `alembic/versions/20260909_0015_make_thread_listing_id_nullable.py`.
- `message` (0001): `id` PK, `thread_id` FK, `direction` String(10), `sent_at`, `text_original` Text, `lang_original` String(10), `text_ru` Text, `answered_by` String(20), `template_key` String(50), `escalated` Bool, `source_evidence` JSONB. — Источник: `alembic/versions/20260907_0001_initial_schema.py:143-157`.
- `deal` (0001 + 0009): `id` PK, `listing_id` FK (nullable с 0009), `agreed_price` Numeric(12,2), `currency` String(3), `price_base` Numeric(12,2), `delivery_method` String(50), `payment_method` String(50), `status` String(30), `closed_at`, `closure_evidence` JSONB; добавлено `lot_id` String(36), `party_id` String(100) (0009). — Источник: `alembic/versions/20260907_0001_initial_schema.py:160-173`, `alembic/versions/20260908_0009_add_lot_reservation_and_deal_constraint.py:26-28`.
- `fx_rate` (0001): PK(`rate_date`, `currency`), `rate_to_base` Numeric(12,6), `source` String(50), `fetched_at`. — Источник: `alembic/versions/20260907_0001_initial_schema.py:176-184`.
- `buy_request` (0001): `id` PK, `query` String(255), `attrs` JSONB, `budget` Numeric(12,2), `currency` String(3), `status` String(30). — Источник: `alembic/versions/20260907_0001_initial_schema.py:187-195`.
- `buy_candidate` (0001): `id` PK, `request_id` FK, `marketplace_key` FK, `external_url`, `price`, `currency`, `price_base`, `verdict` Text, `contacted` Bool. — Источник: `alembic/versions/20260907_0001_initial_schema.py:198-210`.
- `approval` (0001): `id` PK, `item_code` FK, `payload` JSONB, `payload_hash` String(64) unique, `approved_at`, `channel` String(20). — Источник: `alembic/versions/20260907_0001_initial_schema.py:213-224`.
- `job` (0001): `id` PK, `kind` String(50), `payload` JSONB, `state` String(30), `attempts` Int, `run_after`, `locked_by` String(100), `last_error` Text, `idempotency_key` String(255) unique. — Источник: `alembic/versions/20260907_0001_initial_schema.py:227-238`.
- `audit_event` (0001): `id` PK, `at`, `actor` String(20), `action` String(100), `entity` String(50), `entity_id` String(255), `data` JSONB. Trigger запрещает UPDATE/DELETE на этой таблице. — Источник: `alembic/versions/20260907_0001_initial_schema.py:241-262`.
- `truth_log` (0001): `id` PK, `at`, `claim` Text, `recipient` String(30), `evidence` JSONB (CHECK `evidence != '{}'`). — Источник: `alembic/versions/20260907_0001_initial_schema.py:265-275`.
- `lot` (0008 + 0018 + 0009): `id` PK, `code` String(3) unique, `item_id` String(36) FK→item.code ON DELETE CASCADE, `status` String(50) default `DRAFT`, `price_gross` Numeric(12,2), `price_floor` Numeric(12,2), `currency` String(3) default `CZK`, `shipping_policy` String(20) default `BUYER_PAYS`, `notes` Text; добавлено `price_base` Numeric(12,2), `base_rate_date` Date (0018); `reserved_for_party_id` String(100), `reserved_until` (0009). — Источник: `alembic/versions/20260908_0008_add_lot_entity_and_separation.py:21-36`, `alembic/versions/20260911_0018_currency_model_v1.py:54-55`, `alembic/versions/20260908_0009_add_lot_reservation_and_deal_constraint.py:22-23`.
- `lot_pricing` (0004): `id` PK, `item_code` String(3) FK unique, `price_gross`, `price_base`, `price_floor`, `negotiable` Bool default true, `shipping_policy` String(20), `shipping_included_amount` Numeric(10,2), `price_decay_steps` JSONB, `stale_days` Int default 14. **Существует параллельно с `lot`; в коде не используется (см. §14).** — Источник: `alembic/versions/20260908_0004_add_lot_pricing.py:22-38`.
- `carrier`, `broker`, `shipping_service`, `delivery_option` (0003 + 0016): логистика. `delivery_option`: `item_code` FK, `carrier_key`, `service_name`, `price`, `is_manual_price`, `is_enabled`, `broker_key`. — Источник: `alembic/versions/20260908_0003_add_shipping_tables.py`, `alembic/versions/20260909_0016_add_broker_and_service_keys.py`.
- `item_defect` (0007, нормализовано 0017): `item_code` FK, `defect_type`, `severity`, `description_owner`, `description_cs`, `photo_id` (обязательное), `crop_box` JSONB, `acknowledged_by_buyer`. — Источник: `alembic/versions/20260908_0007_add_item_defect_table.py`, `alembic/versions/20260910_0017_normalize_item_defects.py`.
- `party`, `handover`, `settlement` (0011), `payment_option` (0012). — Источник: `alembic/versions/20260908_0011_add_party_handover_settlement_tables.py`, `alembic/versions/20260908_0012_add_payment_option_table.py`.
- `channel_canary` (0020), `android_bridge_lock` + `android_bridge_daily_usage` (0021). — Источник: `alembic/versions/20261003_0020_add_channel_canary.py`, `alembic/versions/20261004_0021_android_bridge_lock_and_usage.py`.
- `outbox` (0002), `price_history` (0005), media-пайплайн (0006). — Источник: `alembic/versions/20260907_0002_add_outbox_table.py`, `20260908_0005_add_price_history.py`, `20260908_0006_add_media_pipeline_tables.py`.

**Ключевые связи:**
- «Черновик» (draft) — это **не таблица**, а `item` со `status=DRAFT` + `attrs["wizard_selected"]`. — Источник: `src/nextlife/services/wizard_textgen.py:20`, `src/nextlife/services/lot_creation.py`.
- `Item` (физический, PK `code` String(3)) и `Lot` (коммерческий, FK `item_id`→item.code, own unique `code` String(3)) — две параллельные сущности, 1:1. — Источник: `alembic/versions/20260908_0008_add_lot_entity_and_separation.py:31`.
- Поле «место хранения на складе» / warehouse / storage_location — **НЕ РЕАЛИЗОВАНО** (нет ни в `item`, ни в `lot`, ни в другой таблице). — Источник: `grep -rn "storage_location\|warehouse" src/nextlife/storage/models/*.py` → пусто.

## 2. Статусы

**Статусы Item** (`ItemStatus` StrEnum): DRAFT, RESEARCHED, MEDIA_READY, AWAITING_APPROVAL, APPROVED, PUBLISHING, PUBLISHED, PARTIALLY_PUBLISHED, BLOCKED, RESERVED, SOLD, RETURNED, ARCHIVED, CANCELLED. Переходы задаёт словарь `TRANSITIONS`, проверяет `validate_transition()`. — Источник: `src/nextlife/core/status.py:8`, `src/nextlife/core/status.py:25`.

**Статусы Lot:** default `DRAFT` (DDL), soft-delete `DELETED`. — Источник: `alembic/versions/20260908_0008_add_lot_entity_and_separation.py:25` (server_default `DRAFT`), `src/nextlife/services/lot_delete.py` (`lot.status = "DELETED"`).

**Статусы Listing** (`listing.status` String(50), без enum в коде):
- `PUBLISHED` — при публикации. — Источник: `src/nextlife/services/publish.py:206`, `src/nextlife/services/relisting.py:172`.
- `ARCHIVED` — при снятии/релистинге. — Источник: `src/nextlife/services/unpublish.py:210`, `src/nextlife/services/relisting.py:154`.
- `ACTIVE` — создаётся в deal/inbox/reservation. — Источник: `src/nextlife/comms/inbox.py:77`, `src/nextlife/services/deals.py:49`, `src/nextlife/services/reservation.py:152`, `src/nextlife/services/payment_confirmation.py:159`.

**Коды предметов** (`core/codes.py`): `ALL_CODES = 000…999`. `allocate_code()` — случайный из свободных; ARCHIVED коды освобождаются после 90-дневного cooldown (`code_release_days`); CANCELLED/DELETED/SOLD коды **никогда не переиспользуются**. — Источник: `src/nextlife/core/codes.py:40`, `src/nextlife/services/lot_creation.py` (`allocate_code(session, code_release_days=90)`).

**Смена статуса Item → SOLD при закрытии сделки.** — Источник: `src/nextlife/services/deals.py:55-59` (`validate_transition(ItemStatus(item.status), ItemStatus.SOLD)`).

---

## 3. Мастер создания (wizard)

**Два мастера в коде:**
- Легаси 5-шаговый: `/ui/wizard` и `/ui/wizard/{code}?step=N` (шаги 1–5), шаблон `wizard.html`. — Источник: `src/nextlife/api/routes_lots.py:183` (`screen_wizard_step`), `src/nextlife/api/routes_lots.py:239-322` (переходы step 2→5).
- Новый lot-wizard: `/ui/lot-wizard` → редирект на `/search`; шаги: search → text → finalize (frames) → publish (dry-run + bazos). Шаблоны `lot_wizard_*.html`. — Источник: `src/nextlife/api/routes_lots.py`, `templates/web/lot_wizard_search.html`, `lot_wizard_text.html`, `lot_wizard_finalize.html`, `lot_wizard_publish.html`.

**Создание Item + Lot одним транзактом** `create_item_and_lot()`; валюта лота = валюта enabled-площадок (должна быть единой). — Источник: `src/nextlife/services/lot_creation.py`.

**Лимит черновиков: 5, только DRAFT.** — Источник: `src/nextlife/services/lot_creation.py`, git log `4762bac` («Add wizard draft cap (5, DRAFT-only)»).

**Срочность live-поиска: `WIZARD_SEARCH_TIMEOUT_S = 900.0`.** — Источник: `src/nextlife/api/routes_lots.py`.

**Кнопка «Опубликовать на Bazoš» в мастере** (dry-run + real). — Источник: `src/nextlife/api/routes_lots.py` (`/ui/lot-wizard/publish`), `templates/web/lot_wizard_publish.html`.

## 4. Площадки (marketplaces)

**11 коннекторов** в `CONNECTOR_MAP`: bazos_cz, sbazar_cz, aukro_cz, hyperinzerce_cz, annonce_cz, trhknih_cz, vinted, kleinanzeigen_de, olx_pl, willhaben_at, facebook_marketplace. `sync_marketplaces()` синхронизирует config YAML → DB. — Источник: `src/nextlife/connectors/registry.py`.

**publish/update/unpublish по каждому коннектору:**
- `bazos_cz` — **реальная реализация** (Playwright web-автоматизация) в `services/bazos_publish.py`, НЕ в коннекторе: `bazos_login_start/confirm/check`, `bazos_publish_listing` (форма `form[action='/insert.php']` на `pc.bazos.cz/pridat-inzerat.php`). **НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ** (live-публичное объявление не создавалось; dry-run пройден). — Источник: `src/nextlife/services/bazos_publish.py`, git log `c4985fe`.
- `aukro_cz` — publish = FEED-канал (XML). — Источник: `src/nextlife/connectors/aukro.py`.
- `sbazar_cz` — **НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ**; search возвращает 404 при отсутствии результатов. — Источник: `src/nextlife/connectors/sbazar.py`, memory `sbazar-404-no-results`.
- Остальные (hyperinzerce, annonce, trhknih, vinted, kleinanzeigen, olx, willhaben, facebook) — `publish`/`update`/`unpublish` **ЗАГЛУШКА**: бросают `ConnectorError` / `CapabilityNotSupported`. — Источник: `src/nextlife/connectors/*.py`.
- `vinted` — publish = dry-run skeleton (бросает `CapabilityNotSupported` на live). — Источник: `src/nextlife/connectors/vinted.py`.
- `annonce_cz` — 403 Cloudflare; обход через `curl_cffi` Chrome-имперсонация. — Источник: git log `95b231d`.

**Валюты площадок:** CZK (большинство), EUR (kleinanzeigen_de), PLN (olx_pl). — Источник: `grep "currency:" config/marketplaces/*.yaml` → CZK/EUR/PLN.

**ANDROID-площадки** `enabled=False` в реестре (web-publish требует ручного enable). — Источник: memory `android-marketplace-enabled-false`.

---

## 5. Тексты

**Блоки текста предмета** `get_item_text_blocks(item)` → `{notes, generated_desc, final_text}`; fallback-шаблон; `is_cyrillic_dominant()` — эвристика ≥50% кириллицы. — Источник: `src/nextlife/services/draft_text.py:15`, `src/nextlife/services/draft_text.py:41`.

**LLM-генерация заголовка+описания** `generate_description()`: при настроенном LLM-endpoint — `mode:"live"`; без endpoint или при ошибке — `mode:"placeholder"`, `PLACEHOLDER_TEXT = "Описание будет сгенерировано после подключения LLM"`. Результат хранится в `attrs["wizard_generated_text"]`. Язык генерации — русский (prompt «…на русском языке»). — Источник: `src/nextlife/services/wizard_textgen.py:16`, `src/nextlife/services/wizard_textgen.py:69`, `src/nextlife/services/wizard_textgen.py:125-152`.

**Финальный текст** `/ui/draft/{code}` + `/update` — пишет `item.notes` + `attrs.final_text`, сбрасывает статус в DRAFT. — Источник: `src/nextlife/api/routes.py`, `templates/web/draft.html`.

**LLM-endpoint настроен** для фильтра категории лота и текста. — Источник: memory `llm-endpoint-for-filter-and-text`.

## 6. Фото

**Операции** (реальный PIL): `rotate_photo`, `crop_photo`, `remove_dust_photo` (MedianFilter(3)), `clean_background_photo` (ImageOps.autocontrast), `toggle_defect_role`, `delete_photo_and_derived`. ORIGINAL (Tier-1) неизменяем. — Источник: `src/nextlife/media/ops.py`.

**Логические цепочки** через `item_photo.derived_from_id` (self-FK); PUBLISH-производные перегенерируются при правке. — Источник: `alembic/versions/20260907_0001_initial_schema.py:48` (`derived_from_id`), `src/nextlife/media/ops.py`.

---

## 7. Доставка (shipping)

**Стандартные тарифы** `STANDARD_TARIFFS` (hardcoded CZK): pickup(0), zasilkovna(89/149 >5000g), balikovna(79), cpost(129/169/229), courier(129/199), ppl(76/78/119), gls(69/109/158). `calculate_carrier_tariff()` сначала пробует `ShippingService` из DB, fallback в `STANDARD_TARIFFS`. — Источник: `src/nextlife/services/shipping_options.py:33-71`, `src/nextlife/services/shipping_options.py:74-100`.

**Перевозчики** `CarrierName` StrEnum: PACKETA, BALIKOVNA, CESKA_POSTA, PPL, DPD, GLS, PERSONAL. — Источник: `src/nextlife/shipping/schema.py`.

**Сущности:** `Carrier`, `Broker`, `ShippingService` (carrier_key, weight_max_g, price, currency, cod_supported, cod_fee, insurance, countries, fetched_at), `DeliveryOption` (item_code, service_id, carrier_key, price, is_manual_price, is_enabled). — Источник: `src/nextlife/storage/models/shipping.py`.

**Итоги** `calculate_totals()` по `shipping_policy`: BUYER_PAYS / SELLER_PAYS / PICKUP_ONLY. — Источник: `src/nextlife/services/shipping_options.py`.

## 8. Цена / валюта

**Символы валют** `CURRENCY_SYMBOLS = {"CZK": "Kč", "EUR": "€"}` (нет PLN!). — Источник: `src/nextlife/money/money.py:57-60`.

**Курс:** `convert_price()` использует ECB-курсы из `fx_rate`; `refresh_fx_rates()` парсит ECB XML. База — `base_currency EUR` (config). — Источник: `src/nextlife/money/money.py`, `config/nextlife.yaml`.

**Поля цены лота:** `price_gross`, `price_floor`, `currency` (default CZK), `price_base`, `base_rate_date`, `shipping_policy`. — Источник: `alembic/versions/20260908_0008_add_lot_entity_and_separation.py`, `20260911_0018_currency_model_v1.py:54-55`.

**Отсутствие валюты предмета = ошибка** (умолчание EUR удалено). — Источник: `src/nextlife/services/sell.py:96-100`.

**Формат** `format_money()` — единый для всех исходящих данных. — Источник: `src/nextlife/money/money.py:71-97`.

---

## 9. Склад и доставка

**Место хранения / склад (warehouse, storage_location) — НЕ РЕАЛИЗОВАНО.** Нет ни одной колонки/поля для расположения на складе в `item`, `lot` или другой таблице. — Источник: `grep -rn "storage_location\|warehouse\|склад" src/nextlife/storage/models/*.py` → пусто.

**Доставка (logistics) реализована** как `DeliveryOption`/`ShippingService`/`Carrier`/`Broker` (см. §7). Это способ доставки покупателю, не хранение на складе. — Источник: `src/nextlife/storage/models/shipping.py`.

**`data_dir`** = `/var/lib/nextlife` (фото, данные) — не «склад» в смысле инвентаря. — Источник: `systemctl cat nextlife-web` → `NEXTLIFE_DATA_DIR=/var/lib/nextlife`.

## 10. Research и аудит

**Research:** `research_run` + `research_result` (поиск эталонных объявлений по площадкам). — Источник: `alembic/versions/20260907_0001_initial_schema.py:77-108`, `src/nextlife/services/wizard_search.py`.

**Аудит:** `audit_event` (at, actor AGENT/OWNER/SYSTEM, action, entity, entity_id, data JSONB) + trigger запрещает UPDATE/DELETE. — Источник: `alembic/versions/20260907_0001_initial_schema.py:241-262`.

**Truth log:** `truth_log` (claim, recipient, evidence JSONB, CHECK evidence != '{}'). — Источник: `alembic/versions/20260907_0001_initial_schema.py:265-275`.

**Audit action при удалении лота** `LOT_DELETED`. — Источник: `src/nextlife/services/lot_delete.py`.

---

## 11. OpenClaw API gateway

**Шлюз:** `SKILL.md` (манифест CLI-skill) + маршруты `/api/oc/*` (`routes_openclaw.py`, `routes_openclaw_ops.py`): status, items, items/{code}, research, draft, publish, listings, search, wizard/*, outbox, inbox, deals/close, canaries. — Источник: `SKILL.md`, `src/nextlife/api/routes_openclaw.py`.

**Аутентификация:** `check_openclaw_auth` через `NEXTLIFE_OPENCLAW_TOKEN`. — Источник: `src/nextlife/api/routes_openclaw.py`.

**Реальных вызовов не найдено:** `journalctl -u nextlife-web --since "30 days ago" | grep -c "api/oc/"` → **0**. **НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ.** — Источник: вывод команды (0 совпадений).

**CLI** = тонкий HTTP-клиент к `127.0.0.1:8081`. — Источник: `SKILL.md`.

## 12. Интерфейс (web)

**Шаблоны** (`templates/web/*.html`): base, dashboard, items, item_detail, create, draft, lot_edit, archive, marketplaces, marketplace_preview, settings, login, deal, threads, inbox, shipping_options, inspection_checklist, error, wizard, lot_wizard_search, lot_wizard_text, lot_wizard_finalize, lot_wizard_publish, _wz_result_cards, _research_panel. — Источник: `ls templates/web/`.

**Список предметов** `/ui/items`, карточка `/ui/items/{item_code}`. `listings_count` считает **ВСЕ** listings предмета (без фильтра по статусу). — Источник: `src/nextlife/api/routes.py:221`.

**Mobile CSS** (NextLife_Web_2.md) завершён 2026-10-14. — Источник: memory `mobile-css-redesign-done`.

**Дизайн-система:** поля форм используют классы `wz-input`/`wz-select`. — Источник: memory `wizard-form-fields-design-system`.

---

## 13. Тесты

**133 тестовых файла** (`tests/*.py` + `tests/e2e/*.py`). — Источник: `ls tests/*.py tests/e2e/*.py | wc -l` → 133.

**Покрытие коннекторов/доменов:** test_bazos_connector, test_aukro_connector, test_sbazar_connector, test_openclaw_api, test_unpublish, test_relisting, test_shipping_*, test_api_photo_*, test_item_lot_separation, test_ui_item_text_blocks и др. E2E: `tests/e2e/test_dry_run.py`, `test_mobile.py`. — Источник: `ls tests/`.

**Базовая линия `make test`:** server-зависимые падения — environmental. — Источник: memory `make-test-server-dependent-failures`.

## 14. Рудименты и дубли

**`lot_pricing` (0004) — рудимент.** Таблица создана (unique `item_code`), но в коде не используется; заменена таблицей `lot` (0008). ORM-модель `LotPricing` существует, но не применяется к потоку. — Источник: `alembic/versions/20260908_0004_add_lot_pricing.py:22-38`, `src/nextlife/pricing/models.py:24`.

**Две параллельные сущности Item / Lot** (1:1) — потенциальное дублирование цены/статуса. — Источник: `alembic/versions/20260908_0008_add_lot_entity_and_separation.py:21-36`.

**`listings_count` считает все listings** (без учёта status) — риск неверного счёта активных объявлений. — Источник: `src/nextlife/api/routes.py:221`.

**Коды CANCELLED/DELETED/SOLD не переиспользуются** (только ARCHIVED после 90 дней) — пул кодов (1000) конечен. — Источник: `src/nextlife/core/codes.py:40`.

**Два мастера создания** (легаси `/ui/wizard` 5-шаговый + новый `/ui/lot-wizard`) одновременно в коде. — Источник: `src/nextlife/api/routes_lots.py:183`.

---

## Итоговая таблица статусов

| Механизм | Статус |
|---|---|
| Модель данных (item/lot/photos/listings/research/deals) | РЕАЛИЗОВАНО |
| Статусы Item (ItemStatus + TRANSITIONS) | РЕАЛИЗОВАНО |
| Статусы Lot (DRAFT/DELETED) | РЕАЛИЗОВАНО |
| Статусы Listing (PUBLISHED/ARCHIVED/ACTIVE) | РЕАЛИЗОВАНО |
| Коды предметов (allocate_code, 90-day release) | РЕАЛИЗОВАНО |
| Мастер создания (2 мастера: legacy + lot-wizard) | РЕАЛИЗОВАНО |
| Bazoš publish (Playwright, login SMS) | НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ |
| Aukro publish (FEED XML) | ЗАГЛУШКА |
| sbazar/hyperinzerce/annonce/trhknih/vinted/kleinanzeigen/olx/willhaben/facebook publish | ЗАГЛУШКА |
| Тексты (draft_text + LLM generate_description) | РЕАЛИЗОВАНО |
| Фото (PIL ops, логические цепочки) | РЕАЛИЗОВАНО |
| Доставка/логистика (DeliveryOption/ShippingService) | РЕАЛИЗОВАНО |
| Цена/валюта (ECB, CURRENCY_SYMBOLS CZK/EUR) | РЕАЛИЗОВАНО |
| Склад / место хранения (warehouse) | НЕ РЕАЛИЗОВАНО |
| Research (research_run/result) | РЕАЛИЗОВАНО |
| Аудит (audit_event + trigger, truth_log) | РЕАЛИЗОВАНО |
| OpenClaw API gateway (/api/oc/*) | РЕАЛИЗОВАНО (НЕ ПРОВЕРЕНО В РЕАЛЬНОСТИ) |
| Интерфейс (web, mobile CSS) | РЕАЛИЗОВАНО |
| Тесты (133 файла) | РЕАЛИЗОВАНО |
| `lot_pricing` (0004) | Рудимент (НЕ ИСПОЛЬЗУЕТСЯ) |
