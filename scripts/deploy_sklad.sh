#!/usr/bin/env bash
# NextLife «Склад» deploy — ТЗ «Склад» вместо «Ленты», этап 7.
#
# Запускается владельцем от root:
#   sudo bash scripts/deploy_sklad.sh
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
