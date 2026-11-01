# NextLife UI Components

> **Источник стилей**: `src/nextlife/api/static/app.css`
> **Tailwind**: не используется (N2). Все раскладки и стили — рукописные компоненты на токенах.
> **Библиотеки**: Alpine.js + HTMX из `static/vendor/` (см. `docs/vendor.md`).

Этот файл — обязательный каталог компонентов (правило U-5). Класс, который
используется в шаблоне, обязан:
1. существовать в `app.css` (U-1);
2. быть описан здесь в формате `` `.имя` `` (U-5);
3. не называться именем утилиты Tailwind (N1-08).

Линтер `scripts/check_css_contract.py` считает класс задокументированным,
только если встречается точное имя в обратных кавычках с точкой: `` `.имя` ``.

---

## Токены (`:root`)

Все цвета, отступы, размеры шрифта, радиусы и тени объявлены токенами (N2-01).
В компонентах не используются «голые» значения, кроме ширины границ 1px/2px.

| Группа | Токены |
| --- | --- |
| Поверхности | `--c-bg`, `--c-surface`, `--c-surface-2`, `--c-surface-3`, `--c-nav-bg` |
| Границы | `--c-border`, `--c-border-md` |
| Текст | `--c-text`, `--c-text-muted`, `--c-text-dim`, `--c-text-faint`, `--c-white` |
| Акцент / инфо | `--c-accent`, `--c-accent-light`, `--c-accent-bright`, `--c-accent-ring`, `--c-info`, `--c-info-bdr`, `--c-info-text` |
| Успех / предупреждение / опасность | `--c-success*`, `--c-warning*`, `--c-danger*` |
| Тёмный протокол | `--c-ink`, `--c-ink-soft`, `--c-ink-text`, `--c-ink-text-2` |
| Отступы (4/8/12/16/24/32/48) | `--sp-1` … `--sp-8` |
| Шкала шрифта (12/14/16/20/24/32) | `--fs-xs`, `--fs-sm`, `--fs-base`, `--fs-lg`, `--fs-xl`, `--fs-2xl` |
| Радиусы | `--r-sm`, `--r-md`, `--r-lg`, `--r-xl`, `--r-3xl`, `--r-full` |
| Тени | `--shadow-sm`, `--shadow-md`, `--shadow-lg`, `--shadow-hover`, `--shadow-danger` |
| Размеры | `--content-w`, `--app-min-w`, `--nav-h`, `--tap-min`, `--thumb-s`, `--thumb-md`, `--modal-max-w`, `--variant-min`, `--variant-h`, `--photo-cell-min`, `--brand-icon-s`, `--dot-s`, `--progress-h`, `--check-s`, `--icon-btn-xs`, `--icon-btn-s` |
| Z-index | `--z-nav`, `--z-modal`, `--z-lightbox`, `--z-toast` |

Цель нажатия не меньше 44×44px — `--tap-min` (N5, WCAG).

---

## Раскладка

```html
<div class="row">…</div>            <!-- горизонтальный ряд, gap var(--sp-2) -->
<div class="row tight">…</div>      <!-- узкий gap var(--sp-1) -->
<div class="row between">…</div>    <!-- justify-content: space-between -->
<div class="row center">…</div>     <!-- justify-content: center -->
<div class="row wrap">…</div>       <!-- flex-wrap: wrap -->
<div class="row align-start">…</div><!-- align-items: flex-start -->

<div class="stack-1">…</div>        <!-- gap var(--sp-1) -->
<div class="stack-2">…</div>        <!-- gap var(--sp-2) -->
<div class="stack-3">…</div>        <!-- gap var(--sp-3) -->
<div class="stack-4">…</div>        <!-- gap var(--sp-4) -->
```

- `.row` — flex-ряд, по центру по вертикали.
- `.tight` — модификатор сужающий gap до `--sp-1` (на `.row`).
- `.between` — разнесение по краям.
- `.center` — центрирование по горизонтали.
- `.wrap` — перенос элементов на новую строку.
- `.align-start` — выравнивание по верхнему краю.
- `.stack` — вертикальный столбец (основной).
- `.stack-1` — вертикальный столбец, gap `--sp-1`.
- `.stack-2` — вертикальный столбец, gap `--sp-2`.
- `.stack-3` — вертикальный столбец, gap `--sp-3`.
- `.stack-4` — вертикальный столбец, gap `--sp-4`.

Сетки:

```html
<div class="grid-2">…</div>   <!-- 2 колонки -->
<div class="grid-3">…</div>   <!-- 3 колонки -->
<div class="grid-auto">…</div><!-- auto-fill minmax -->
<div class="grid">…</div>     <!-- базовая сетка -->
```

- `.grid` — базовый grid-контейнер.
- `.grid-2`, `.grid-3` — сетки в 2 и 3 колонки.
- `.grid-auto` — автоколонки `minmax`.

Прочие утилиты раскладки:

- `.grow` — `flex: 1` (расти в свободное место ряда).
- `.centered` — центрирование по горизонтали (margin auto).
- `.scroll-box` — ограничиваемая по высоте область прокрутки.
- `.pre` — `white-space: pre-wrap` (сохранение переносов, напр. протоколы).
- `.divider` — горизонтальный разделитель.

---

## Типографика и текст

- `.fs-xs`, `.fs-sm`, `.fs-lg`, `.fs-xl` — размеры шрифта по шкале.
- `.fw-medium`, `.fw-semibold`, `.fw-bold` — начертание (500/600/700).
- `.mono` — моноширинный шрифт.
- `.italic` — курсив.
- `.ls-wide` — увеличенный межбуквенный интервал.
- `.text-center`, `.text-right` — выравнивание текста.
- `.hash` — моноширинный хеш с `word-break: break-all`.

Цвета текста (по токенам):

- `.text-muted`, `.text-dim`, `.text-faint` — приглушённые оттенки.
- `.text-accent` — акцентный.
- `.text-success`, `.text-warning`, `.text-danger` — смысловые цвета.

---

## `.hidden` — утилита скрытия (N2-05)

```html
<div class="hidden">Скрыт до снятия класса</div>
```

Объявлена один раз: `display: none !important`. Все места скрытия (модальные
окна, редактируемые блоки) используют именно её. Скрытие из JS:
`el.classList.add('hidden')` / `el.classList.remove('hidden')`.

---

## Кнопки и ссылки

```html
<button class="btn">Действие</button>
<button class="btn btn-primary">Основное</button>
<button class="btn btn-secondary">Вторичное</button>
<button class="btn btn-danger">Опасное</button>
<button class="btn btn-success">Успех</button>
<button class="btn btn-dark">Тёмное</button>
<button class="btn btn-sm">Маленькое</button>
<a href="…" class="btn btn-inline">Ссылка-кнопка</a>
<a href="…" class="link-btn">Текстовая ссылка-кнопка</a>
```

- `.btn` — базовая кнопка, min-height 44px.
- `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-success`, `.btn-dark` — цветовые варианты.
- `.btn-sm` — уменьшенная (высота 44px сохраняется).
- `.btn-inline` — кнопка-ссылка «в потоке» (не во всю ширину).
- `.link-btn` — текстовая ссылка с площадью нажатия 44px.
- `.grow` — кнопка на всю ширину ряда (с `.row`).

---

## Карточки и блоки

```html
<div class="card">…</div>
<a href="…" class="card card-link">…</a>
<div class="card">
  <div class="card-title">Заголовок</div>
  <div class="card-cta">Перейти →</div>
</div>
```

- `.card` — базовый контейнер (фон, радиус, тень, padding).
- `.card-link` — карточка-ссылка (flex-колонка, hover-тень).
- `.card-title` — заголовок внутри карточки (с высотой нажатия 44px для ссылки).
- `.card-cta` — строка призыва к действию внизу карточки.

Вспомогательные блоки:

- `.box` — нейтральная коробка с рамкой.
- `.box-subtle` — приглушённая (фон `surface-2`).
- `.box-accent` — акцентная.
- `.box-dashed` — пунктирная (пустые состояния форм).

---

## Статусы и бейджи

```html
<span class="badge">Нейтрально</span>
<span class="badge badge-primary">Первичный</span>
<span class="badge badge-secondary">Вторичный</span>
<span class="badge badge-success">Успех</span>
<span class="badge badge-warning">Ожидает</span>
<span class="badge badge-danger">Опасность</span>
<span class="badge badge-info">Инфо</span>
<span class="badge badge-active">Активен</span>
<span class="badge badge-defect">Дефект</span>
<span class="badge badge-awaiting">Ожидает</span>
<span class="badge badge-approved">Утверждено</span>
<span class="badge badge-ai">AI</span>
<span class="badge badge-vlm">VLM</span>
<span class="count-badge">5</span>
<span class="chip mono">Код</span>
```

- `.badge` — базовый бейдж статуса.
- `.badge-primary`, `.badge-secondary`, `.badge-success`, `.badge-warning`, `.badge-danger`, `.badge-info`, `.badge-active`, `.badge-defect`, `.badge-awaiting`, `.badge-approved`, `.badge-ai`, `.badge-vlm` — варианты.
- `.count-badge` — счётчик-бейдж.
- `.chip` — «чип» (код лота, метод доставки).

---

## Цена

- `.price` — оформление цены (крупный, акцент).

---

## Заголовки страниц и секций

```html
<div class="page-head">
  <div>
    <h1 class="page-title">Заголовок</h1>
    <p class="page-subtitle">Подзаголовок</p>
  </div>
  <span class="badge">…</span>
</div>

<h2 class="section-title">Секция</h2>      <!-- мелкая капитель -->
<div class="section-label">Подпись</div>   <!-- подпись секции -->
<h3 class="subhead">Заголовок блока</h3>   <!-- блок внутри страницы -->
```

- `.page-head` — шапка страницы (заголовок + действие по краям).
- `.page-title` — главный заголовок (fs-xl, bold).
- `.page-subtitle` — подзаголовок.
- `.section-title` — заглавная подпись секции (fs-xs, uppercase).
- `.section-label` — подпись над секцией (fs-sm, uppercase).
- `.subhead` — заголовок блока внутри страницы (archive).

---

## Формы

```html
<div class="field">
  <label class="field-label" for="x">Метка</label>
  <input id="x" class="input-text" type="text">
  <span class="field-hint">Подсказка</span>
  <span class="field-error">Ошибка</span>
</div>

<label class="check-row">
  <input type="checkbox"> Подписать
</label>

<form class="form-inline">…</form>
<input class="cost-input" type="number">
```

- `.field` — обёртка поля формы (stack).
- `.field-label` — метка поля.
- `.field-hint` — подсказка под полем.
- `.field-error` — текст ошибки поля.
- `.input-text` — базовый input/select/textarea (min-height 44px).
- `.check-row` — строка чекбокса (label 44px, чекбокс 20px).
- `.form-inline` — инлайн-форма (рядом).
- `.cost-input` — узкое числовое поле (тариф доставки).

---

## Пустые состояния

```html
<div class="empty">Пусто. <a class="btn btn-sm">Действие</a></div>
<div class="empty empty-sm">Мелкое пустое</div>
```

- `.empty` — пустое состояние с пояснением и кнопкой действия.
- `.empty-sm` — уменьшенное.

---

## Модальные окна (N3-04)

```html
<div id="modal" class="modal-overlay hidden">
  <div class="modal-box">
    <h3>Заголовок</h3>
    <div class="row tight">
      <button class="btn btn-secondary grow" onclick="close()">Отмена</button>
      <button class="btn grow">Подтвердить</button>
    </div>
  </div>
</div>
```

- `.modal-overlay` — полноэкранная подложка (flex, по центру).
- `.modal-box` — окно (max-width `--modal-max-w`).
- `.modal` — базовый класс модалки.

Поведение (глобальный обработчик в `base.html`): закрытие по кнопке, по клику
вне окна, по Escape; фокус переводится внутрь и возвращается обратно; фон не
прокручивается (body overflow hidden). Скрытие — через `.hidden`.

---

## Вкладки

```html
<div class="tabs">
  <a class="tab active">Активная</a>
  <a class="tab">Другая</a>
</div>
```

- `.tabs` — контейнер вкладок (горизонтальная прокрутка).
- `.tab` — вкладка.
- `.active` — активное состояние (вкладки, фильтры, навигация).

---

## Списки

```html
<ul class="list">
  <li>Пункт</li>
</ul>
```

- `.list` — вертикальный список строк (stack, gap sp-2).
- `.list li` — строка списка (flex-ряд).

---

## Уведомления

```html
<div class="toast-banner">Сохранено</div>
<div class="toast-banner warning">Ошибка</div>
```

- `.toast` — базовый toast.
- `.toast-banner` — плашка-уведомление (fixed).
- `.warning` — вариант «предупреждение» (на `.toast-banner`, `.filter-pill`).
- `.spin` — спиннер (анимация вращения, добавляется из JS при обновлении).

---

## Шапка и навигация (base.html)

- `.app-shell` — каркас приложения (столбец, отступы под safe-area).
- `.app-header` — верхняя панель.
- `.brand` — ссылка-бренд.
- `.brand-icon` — иконка бренда (градиент, 36px).
- `.brand-text` — текст бренда.
- `.login-brand` — иконка бренда на экране входа (по центру).
- `.header-actions` — действия в шапке.
- `.header-refresh-btn` — кнопка обновления.
- `.tailscale-status` — индикатор Tailscale.
- `.status-dot` — точка статуса.
- `.bottom-nav` — нижняя навигация (fixed, высота `--nav-h`).
- `.nav-item` — пункт нижней навигации.

---

## Лента предметов (items)

```html
<a class="item-feed-card card">
  <div class="feed-card-body">
    <div class="feed-card-thumb">
      <img class="thumb feed-thumb" src="…">
      <!-- или <div class="thumb feed-thumb-placeholder">📦</div> -->
    </div>
    <div class="feed-card-content">…</div>
  </div>
</a>
```

- `.item-feed-card` — карточка в ленте.
- `.feed-card-body` — тело карточки (ряд: фото + контент).
- `.feed-card-thumb` — ячейка фото в ленте.
- `.feed-card-content` — контент карточки (столбец).
- `.feed-thumb` — фото в ленте.
- `.feed-thumb-placeholder` — плейсхолдер «нет фото».

---

## Миниатюры и галерея

```html
<img class="thumb" src="…">
<div class="thumb-strip">
  <div class="thumb-card active">
    <img src="…">
    <button class="thumb-del-btn">×</button>
  </div>
</div>
<div class="thumb-card wizard-thumb">…</div>
<span class="thumb-role-tag">MAIN</span>
```

- `.thumb` — миниатюра фиксированного размера (object-fit: cover).
- `.thumb-strip` — лента миниатюр (горизонтальная прокрутка).
- `.thumb-card` — карточка-миниатюра в ленте.
- `.thumb-del-btn` — кнопка удаления миниатюры.
- `.thumb-role-tag` — тег роли снимка (MAIN/DEFECT).
- `.wizard-thumb` — миниатюра в мастере создания.
- `.photo-grid` — сетка фото (медиа-менеджер).
- `.photo-cell` — ячейка фото в сетке.

---

## Слайдер и лайтбокс

```html
<div class="slider-container">
  <button class="slider-arrow slider-prev">‹</button>
  <img class="slider-img" src="…">
  <button class="slider-arrow slider-next">›</button>
  <span class="slider-counter">1 / 3</span>
  <span class="slider-role-tag badge badge-active">MAIN</span>
</div>

<!-- Лайтбокс (base.html) -->
<div id="lightbox">
  <button id="lightbox-close">×</button>
  <div class="lightbox-counter">1 / 3</div>
  <button id="lightbox-prev">‹</button>
  <img id="lightbox-img" src="…">
  <button id="lightbox-next">›</button>
  <div class="lightbox-strip thumbnails-strip"></div>
</div>
```

- `.slider-container` — контейнер слайдера.
- `.slider-img` — главное фото слайдера.
- `.slider-arrow` — стрелка слайдера (44px).
- `.slider-prev`, `.slider-next` — стрелки назад/вперёд.
- `.slider-counter` — счётчик слайда.
- `.slider-role-tag` — тег роли фото.
- `.lightbox-counter` — счётчик лайтбокса.
- `.lightbox-strip` — лента миниатюр лайтбокса.
- `.thumbnails-strip` — полоса миниатюр (общая с лайтбоксом).

---

## Фильтры (пилюли)

```html
<div class="filter-pills">
  <a class="filter-pill active">Все</a>
  <a class="filter-pill">Продано</a>
  <a class="filter-pill fraud">Мошенничество</a>
</div>
```

- `.filter-pills` — контейнер фильтров (горизонтальная прокрутка, скрыт скроллбар).
- `.filter-pill` — фильтр (min-height 44px).
- `.fraud` — вариант «подозрение на мошенничество».

---

## Информационные блоки

```html
<div class="info-box info-box-slate">Нейтрально</div>
<div class="info-box info-box-blue">Информация</div>
<div class="info-box info-box-amber">Предупреждение</div>
<div class="info-box info-box-green">Успех</div>
<div class="info-box info-box-red">Ошибка</div>
```

- `.info-box` — базовый блок (padding, радиус).
- `.info-box-slate`, `.info-box-blue`, `.info-box-amber`, `.info-box-green`, `.info-box-red` — цветовые варианты (всегда в паре с `.info-box`).

---

## Прогресс (мастер)

```html
<div class="progress-bar">
  <div class="progress-step done"></div>
  <div class="progress-step done"></div>
  <div class="progress-step"></div>
</div>
```

- `.progress-bar` — контейнер прогресса.
- `.progress-step` — шаг прогресса.
- `.done` — завершённый шаг / статус.

---

## Финансы (сделка)

```html
<div class="finance-box">
  <div class="finance-row"><span>Gross</span><span>1 200 Kč</span></div>
  <div class="finance-row total"><span>Net</span><span>1 080 Kč</span></div>
</div>
```

- `.finance-box` — таблица расчёта.
- `.finance-row` — строка расчёта.
- `.total` — итоговая строка (выделена).

---

## Превью варианта (marketplace_preview)

```html
<div class="variant-grid">
  <div class="variant-card">
    <div class="variant-thumb"><img src="…"></div>
    <div class="variant-meta">
      <div class="row between">
        <span>MAIN</span><span>1200×800</span>
      </div>
    </div>
  </div>
</div>
```

- `.variant-grid` — сетка вариантов изображений.
- `.variant-card` — карточка варианта.
- `.variant-thumb` — картинка варианта (фикс. высота).
- `.variant-meta` — метаданные варианта (столбец).
- `.preview-head` — шапка превью (заголовок + цена, border снизу).
- `.mp-row` — строка возможностей площадки (lot_edit).

---

## Сообщения (threads / inbox)

```html
<div class="msg-bubble msg-bubble-in">
  <div class="row between fs-xs text-muted">
    <span class="fw-semibold">Покупатель</span><span>12:00</span>
  </div>
  <div class="msg-original">Оригинал</div>
  <div class="msg-translation">Перевод</div>
</div>
<div class="msg-bubble msg-bubble-out">Ответ агента</div>
<div class="msg-bubble msg-bubble-in msg-bubble-fraud">Подозрительное</div>
```

- `.msg-bubble` — «пузырь» сообщения (flex-столбец, gap).
- `.msg-bubble-in` — входящее (покупатель).
- `.msg-bubble-out` — исходящее (агент/владелец).
- `.msg-bubble-fraud` — вариант «подозрение».
- `.msg-original` — блок оригинала сообщения.
- `.msg-translation` — блок перевода.
- `.thread-head` — заголовок треда (border снизу).
- `.event-row` — строка события аудита.
- `.fraud-banner` — баннер подозрения на мошенничество.
- `.fraud-reasons` — список причин (втянут).
- `.prepared-response` — подготовленный ответ агента.
- `.action-buttons-group` — группа кнопок действий.
- `.escalated-actions` — блок действий по эскалации (пунктир сверху).
- `.char-limit` — счётчик символов.
- `.over-limit` — превышен лимит символов (красный, bold; ставится из JS).

---

## Экран сделки (deal)

```html
<div class="deal-screen">
  <div class="deal-head">…</div>
  <div class="evidence-row ok">…</div>
  <div class="evidence-row missing">…</div>
</div>
```

- `.deal-screen` — контейнер экрана сделки.
- `.deal-head` — шапка сделки (кнопка назад + статус).
- `.evidence-row` — строка доказательства отправки.
- `.ok` — доказательство загружено (зелёная).
- `.missing` — доказательство отсутствует (нейтральная).

---

## Экран входа (login)

- `.login-card` — карточка входа (max-width, по центру).
- `.login-brand` — бренд на экране входа.

---

## Протокол осмотра (тёмный фрагмент)

```html
<ul class="protocol">
  <li class="protocol-title"><span>📋</span><span>Категория</span></li>
  <li class="protocol-item">
    <input type="checkbox" id="chk_x">
    <label for="chk_x">
      <span class="fw-medium">1. Пункт</span>
      <span class="protocol-sub">Подпункт</span>
    </label>
  </li>
</ul>
```

- `.protocol` — тёмный контейнер протокола (ul, flex-столбец).
- `.protocol-title` — заголовок протокола.
- `.protocol-item` — пункт протокола (flex-ряд: чекбокс + label).
- `.protocol-sub` — вторичный текст пункта.

---

## Состояния-модификаторы (применяются к компонентам)

- `.active` — активный (вкладки, фильтры, навигация, миниатюры).
- `.done` — завершено (шаги прогресса).
- `.ok` / `.missing` — статус доказательства.
- `.total` — итог (строка финансов).
- `.fraud` — мошенничество (фильтры, пузыри).
- `.warning` — предупреждение (toast, фильтр).
- `.over-limit` — превышен лимит (счётчик символов).

---

## Справочно: объявлены, но не используются в шаблонах сейчас

`css`, `modal`, `text-center`, `divider`, `center`, `stack`, `grid`,
`badge-ai`, `badge-approved`, `badge-awaiting`, `badge-vlm`, `count-badge`,
`field-error`, `field-hint`, `info-box-blue`, `info-box-green`, `ls-wide`,
`photo-cell`, `photo-grid`, `tab`, `tabs`, `toast`, `spin`, `over-limit`,
`fraud` — зарезервированы для состояний, динамических вариантов или будущих
экранов. Их наличие в `app.css` не является нарушением (нарушение — только
использование класса, которого нет в CSS, либо не задокументированный
используемый класс).
