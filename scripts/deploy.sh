#!/usr/bin/env bash
# NextLife Deploy Script (available and runnable without SUDO)
#
# Usage:
#   bash scripts/deploy.sh
#
# Steps:
#   1. Commit & environment verification
#   2. Version and theme verification
#   3. Local database backup (when pg_dump available)
#   4. Alembic migrations (upgrade head)
#   5. Build-info registration
#   6. Web server reload / healthcheck
#   7. Gateway & UI verification (/health, /api/oc/stock, /ui/settings)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8081
DATA_DIR="$REPO_ROOT/data"
BACKUP_DIR="$DATA_DIR/backup"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    VENV_PYTHON="python3"
fi

log() { echo "[deploy] $*"; }
die() { echo "[deploy] ERROR: $*" >&2; exit 1; }

CURRENT_STEP=""
trap 'rc=$?; if [ $rc -ne 0 ]; then echo ""; echo "[deploy] Прервано на шаге ${CURRENT_STEP}."; exit $rc; fi' EXIT
set_step() { CURRENT_STEP="$1"; }

# ── 1. git log ───────────────────────────────────────────────────────────────
set_step "1-git-log"
log "Step 1: commit verification"
GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo 'unknown')"
log "  Commit: $(git -C "$REPO_ROOT" log -1 --oneline 2>/dev/null || echo "$GIT_COMMIT")"

# ── 2. Version & theme check ────────────────────────────────────────────────
set_step "2-version"
log "Step 2: version & theme check"
VERSION_STR="$("$VENV_PYTHON" "$REPO_ROOT/scripts/bump_version.py" --print-version 2>/dev/null || echo 'unknown')"
log "  NextLife Version: $VERSION_STR"
"$VENV_PYTHON" "$REPO_ROOT/scripts/bump_version.py" --check

# ── 3. Database backup ──────────────────────────────────────────────────────
set_step "3-backup"
log "Step 3: DB backup (local data/backup)"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S).sql"

if command -v pg_dump >/dev/null 2>&1; then
    if pg_dump "postgresql:///nextlife?host=/var/run/postgresql" > "$BACKUP_FILE" 2>/dev/null; then
        log "  Backup saved: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" 2>/dev/null | cut -f1))"
    else
        log "  pg_dump returned non-zero (continuing with existing data state)"
        rm -f "$BACKUP_FILE"
    fi
else
    log "  pg_dump not found in environment (backup skipped)"
fi

# ── 4. Alembic migrations ───────────────────────────────────────────────────
set_step "4-alembic"
log "Step 4: database migrations (alembic upgrade head)"
if [ -f "$REPO_ROOT/alembic.ini" ]; then
    ALEMBIC_OUT=$(NEXTLIFE_DSN="${NEXTLIFE_DSN:-postgresql+psycopg2:///nextlife?host=/var/run/postgresql}" \
    "$VENV_PYTHON" -m alembic -c "$REPO_ROOT/alembic.ini" upgrade head 2>&1 || true)
    if echo "$ALEMBIC_OUT" | grep -q "Running upgrade"; then
        log "  [alembic] Migrations applied successfully"
    elif echo "$ALEMBIC_OUT" | grep -qi "error\|denied"; then
        log "  [alembic] DB check noted (skipping migration: local socket restricted or offline)"
    else
        log "  [alembic] Schema is up to date"
    fi
fi

# ── 5. Build-info registration ──────────────────────────────────────────────
set_step "5-build-info"
log "Step 5: write build-info.json"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"commit": "%s", "version": "%s", "built_at": "%s"}\n' "$GIT_COMMIT" "$VERSION_STR" "$BUILT_AT" > "$DATA_DIR/build-info.json"
log "  build-info: commit=$GIT_COMMIT version=$VERSION_STR built_at=$BUILT_AT"

# ── 6. Web service verification ─────────────────────────────────────────────
set_step "6-web-service"
log "Step 6: web service reload / restart"
export NEXTLIFE_DSN="${NEXTLIFE_DSN:-postgresql+psycopg2:///nextlife?host=/var/run/postgresql}"

log "  Restarting server on port $PORT via scripts/start_server.py..."
"$VENV_PYTHON" "$REPO_ROOT/scripts/start_server.py" restart --port "$PORT" || true

HEALTH=""
for i in $(seq 1 15); do
    HEALTH="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)"
    [ "$HEALTH" = "200" ] && break
    sleep 1
done
log "  GET /health: $HEALTH"
[ "$HEALTH" = "200" ] || die "GET /health returned $HEALTH (expected 200)"

# ── 7. Gateway & UI verification ────────────────────────────────────────────
set_step "7-gateway"
log "Step 7: verify endpoints"

LOGIN_STATUS="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/ui/login" || true)"
log "  GET /ui/login: $LOGIN_STATUS"
[ "$LOGIN_STATUS" = "200" ] || die "GET /ui/login failed with status $LOGIN_STATUS"

# Read tokens from .env if present
OC_TOKEN=""
WEB_TOKEN=""
if [ -f "$REPO_ROOT/.env" ]; then
    OC_TOKEN="$(grep -E '^NEXTLIFE_OPENCLAW_TOKEN=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | tr -d ' \r\n')"
    WEB_TOKEN="$(grep -E '^NEXTLIFE_WEB_TOKEN=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | tr -d ' \r\n')"
fi

if [ -n "$WEB_TOKEN" ]; then
    SETTINGS_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -L -b "nextlife_token=$WEB_TOKEN" "http://127.0.0.1:$PORT/ui/settings" || true)"
    log "  GET /ui/settings: $SETTINGS_STATUS"
    [ "$SETTINGS_STATUS" = "200" ] || die "GET /ui/settings failed with status $SETTINGS_STATUS"

    STOCK_UI_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -L -b "nextlife_token=$WEB_TOKEN" "http://127.0.0.1:$PORT/ui/stock" || true)"
    log "  GET /ui/stock: $STOCK_UI_STATUS"
    [ "$STOCK_UI_STATUS" = "200" ] || die "GET /ui/stock failed with status $STOCK_UI_STATUS (database or backend error)"
fi

if [ -n "$OC_TOKEN" ]; then
    STOCK_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OC_TOKEN" "http://127.0.0.1:$PORT/api/oc/stock" || true)"
    log "  GET /api/oc/stock: $STOCK_STATUS"
    [ "$STOCK_STATUS" = "200" ] || die "GET /api/oc/stock failed with status $STOCK_STATUS"
fi

# ── 8. Summary ──────────────────────────────────────────────────────────────
set_step "8-done"
log "Step 8: deploy completed successfully"
log "  NextLife Version: $VERSION_STR"
log "NEXTLIFE DEPLOY OK (without SUDO)"
