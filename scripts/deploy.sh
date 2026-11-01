#!/usr/bin/env bash
# NextLife UI Restructure deploy — ТЗ «Реструктуризация интерфейса», этап 11.
#
# Запускается владельцем от root:
#   sudo bash scripts/deploy.sh
#
# Выполняет:
# 1. Резервную копию БД
# 2. Alembic upgrade head
# 3. Перезапуск nextlife-web
# 4. Включение и перезапуск nextlife-worker.service
# 5. Проверку шлюза /api/oc/stock и /api/oc/notifications
#
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

log() { echo "[deploy] $*"; }
die() { echo "[deploy] ERROR: $*" >&2; exit 1; }

CURRENT_STEP=""
trap 'rc=$?; if [ $rc -ne 0 ]; then echo ""; echo "[deploy] Прервано на шаге ${CURRENT_STEP}. Откат: восстановите БД из backup в ${BACKUP_DIR}."; exit $rc; fi' EXIT
set_step() { CURRENT_STEP="$1"; }

# alembic под пользователем nextlife с DSN (peer via unix socket).
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
BACKUP_FILE="$BACKUP_DIR/pre-ui-$(date +%Y%m%d-%H%M%S).sql"
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
set_step "5-restart-web"
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

# ── 6. systemctl enable --now nextlife-worker ────────────────────────────────
set_step "6-worker-service"
log "Step 6: enable and start nextlife-worker.service"
if [ -f "/etc/systemd/system/nextlife-worker.service" ]; then
    systemctl enable --now nextlife-worker.service
    systemctl restart nextlife-worker.service
    WORKER_ACTIVE="$(systemctl is-active nextlife-worker.service || true)"
    log "  nextlife-worker: $WORKER_ACTIVE"
else
    log "  WARNING: /etc/systemd/system/nextlife-worker.service not found on host"
fi

# ── 7. Проверка шлюза OpenClaw (/api/oc/stock, /api/oc/notifications) ───────
set_step "7-gateway"
log "Step 7: verify OpenClaw gateway endpoints"
ENV_FILE="$(systemctl show nextlife-web -p EnvironmentFile --value | head -1)"
[ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ] || die "Service EnvironmentFile not found"
OC_TOKEN="$(grep -E '^NEXTLIFE_OPENCLAW_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
[ -n "$OC_TOKEN" ] || die "NEXTLIFE_OPENCLAW_TOKEN not found in $ENV_FILE"

STOCK_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OC_TOKEN" "http://127.0.0.1:$PORT/api/oc/stock")"
log "  GET /api/oc/stock: $STOCK_STATUS"
[ "$STOCK_STATUS" = "200" ] || die "GET /api/oc/stock returned $STOCK_STATUS (expected 200)"

NOTIF_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OC_TOKEN" "http://127.0.0.1:$PORT/api/oc/notifications")"
log "  GET /api/oc/notifications: $NOTIF_STATUS"
[ "$NOTIF_STATUS" = "200" ] || die "GET /api/oc/notifications returned $NOTIF_STATUS (expected 200)"
unset OC_TOKEN

# ── 8. Финальное состояние ──────────────────────────────────────────────────
set_step "8-done"
log "Step 8: final state"
alembic current
STOCK_COUNT="$(sudo -u "$NEXTLIFE_USER" psql -d "$DB_NAME" -tAc "select count(*) from item where status not in ('DELETED', 'CANCELLED')")"
LISTING_COUNT="$(sudo -u "$NEXTLIFE_USER" psql -d "$DB_NAME" -tAc "select count(*) from listing where status = 'PUBLISHED'")"
log "  Items on Склад: $STOCK_COUNT"
log "  Active listings: $LISTING_COUNT"
log "NEXTLIFE UI DEPLOY OK"
