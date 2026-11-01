# Вендорные библиотеки (N3-01)

CDN в проект не используется. Все библиотеки интерактивности лежат в
`src/nextlife/api/static/vendor/` и подключаются в `templates/web/base.html`
одним разом (N3-02).

| Библиотека | Версия | Файл | Размер, байт | SHA-256 | Источник | Дата фиксации |
| --- | --- | --- | --- | --- | --- | --- |
| Alpine.js | 3.14.8 | `static/vendor/alpine.min.js` | 44758 | `b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643` | https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js | 2026-09-10 |
| HTMX | 1.9.12 | `static/vendor/htmx.min.js` | 48101 | `449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452` | https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js | 2026-09-10 |

## Подключение в base.html

```html
<script src="/static/vendor/htmx.min.js"></script>
<script defer src="/static/vendor/alpine.min.js"></script>
```

Порядок фиксированный: htmx синхронно (не требует DOM), Alpine с `defer`
(инициализируется после парсинга DOM).

## Гарантия работы без сети

Интерфейс обязан работать полностью офлайн. Проверка: прогон прибора с
отключённым внешним выходом (`--offline`) — все экраны PASS, сетевых
запросов за пределы localhost нет.
