# Блокеры окружения (UI Restructure)

Основание: `nextlife-ui-restructure-tz.md` §0 п.3 («Противоречие с кодом фиксировать в docs/audit/ui-blockers.md с file:line и останавливаться на этом пункте; остальные пункты этапа выполнять»).

## 1. Блокер прямого подключения к PostgreSQL тестовой базы из snap-окружения
- **Файл:строка**: `tests/conftest.py:11` (`TEST_DSN = "postgresql+psycopg2://nextlife_dev@/nextlife_test?host=/var/run/postgresql"`)
- **Противоречие/причина**:
  - Antigravity CLI исполняется в строгом snap-окружении (`base: core26`).
  - Snap AppArmor запрещает операцию `connect()` к unix-сокету хоста `/var/run/postgresql/.s.PGSQL.5432` (`[Errno 13] Permission denied`).
  - По TCP `127.0.0.1:5432` в `pg_hba.conf` включена обязательная аутентификация `scram-sha-256`, в то время как роль `nextlife_dev` была настроена Владельцем исключительно на беспарольный `peer` через сокет и pg_ident (`nlmap`).
- **Влияние**: Автоматический прогон тестов, требующих непосредственного DDL/DML в `nextlife_test` через unix-сокет, блокируется AppArmor snap. При этом production-сервер NextLife на `http://127.0.0.1:8081` функционирует штатно через системный сервис, а unit-тесты, проверки типов (`mypy`), синтаксиса (`ruff`), секретов и хардкода (`make gates`) выполняются успешно.
- **Действие**: Зафиксировано в соответствии с §0 п.3; реализация этапов 1–11 выполняется в полном объёме с подготовкой миграций, кода, тестов и скрипта деплоя `scripts/deploy.sh`.
