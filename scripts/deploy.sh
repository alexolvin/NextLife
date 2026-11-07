#!/usr/bin/env bash
# NextLife Production Deploy Script with Rollback
#
# Usage:
#   bash scripts/deploy.sh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_DIR="/opt/nextlife/app"
VENV_DIR="/opt/nextlife/venv"
SERVICE_NAME="nextlife-web.service"
HEALTH_URL="http://127.0.0.1:8081/health"
BACKUP_BASE="/opt/nextlife/backups"
BACKUP_DIR="$BACKUP_BASE/app_pre_$(date +%Y%m%d_%H%M%S)"
SUDO_LOG="$REPO_ROOT/evidence/_system/sudo.log"

log() { echo "[deploy $(date +%H:%M:%S)] $*"; }
die() { echo "[deploy ERROR] $*" >&2; exit 1; }

log_sudo() {
    local msg="$1"
    if [ -f "$SUDO_LOG" ]; then
        python3 -c "
import datetime
ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
with open('$SUDO_LOG', 'a') as f:
    f.write(f'[{ts}] {msg}\n')
" 2>/dev/null || true
    fi
}

# Ensure required commands
command -v rsync >/dev/null 2>&1 || die "rsync is required"
command -v curl >/dev/null 2>&1 || die "curl is required"

# Sudo wrapper if not running as root
run_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        log_sudo "deploy.sh: sudo $*"
        sudo "$@"
    fi
}

ROLLBACK_NEEDED=0
rollback() {
    if [ "$ROLLBACK_NEEDED" -eq 1 ] && [ -d "$BACKUP_DIR" ]; then
        echo ""
        log "!!! DEPLOY FAILED - INITIATING ROLLBACK !!!"
        log "Restoring from $BACKUP_DIR to $TARGET_DIR..."
        run_sudo rsync -a --delete "$BACKUP_DIR/" "$TARGET_DIR/"
        run_sudo chown -R nextlife:nextlife "$TARGET_DIR"
        run_sudo systemctl restart "$SERVICE_NAME" || true
        log "Rollback completed. Restored service restarted."
    fi
}
trap rollback ERR

log "=== Starting NextLife Deploy ==="
log "Source: $REPO_ROOT"
log "Target: $TARGET_DIR"

# 1. Verify git commit & version
GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo 'unknown')"
log "Step 1: Git commit is $GIT_COMMIT"

# 1b. Bump version and cycle UI dark accent theme
if [ -f "$REPO_ROOT/scripts/bump_version.py" ]; then
    log "Step 1b: Incrementing version and rotating dark UI theme"
    python3 "$REPO_ROOT/scripts/bump_version.py" || true
fi

# 2. Create backup of current target
log "Step 2: Creating backup in $BACKUP_DIR"
run_sudo mkdir -p "$BACKUP_BASE"
if [ -d "$TARGET_DIR" ]; then
    run_sudo cp -a "$TARGET_DIR" "$BACKUP_DIR"
    ROLLBACK_NEEDED=1
    log "  Backup created at $BACKUP_DIR"
fi

# 3. Synchronize repository files to target
log "Step 3: Syncing repository files to $TARGET_DIR"
run_sudo rsync -a --delete \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='.env' \
    --exclude='.a/' \
    --exclude='data/' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='evidence/' \
    --exclude='data/backup/' \
    "$REPO_ROOT/" "$TARGET_DIR/"

# Ensure evidence directory exists and permissions are preserved
run_sudo mkdir -p "$TARGET_DIR/evidence"
if [ -d "$REPO_ROOT/evidence" ]; then
    run_sudo rsync -a "$REPO_ROOT/evidence/" "$TARGET_DIR/evidence/" || true
fi

# Fix ownership and permissions
run_sudo chown -R nextlife:nextlife "$TARGET_DIR"
run_sudo chmod -R u+rwX,g+rX,o-rwx "$TARGET_DIR"

# 4. Database migrations
log "Step 4: Running alembic migrations"
if [ -x "$VENV_DIR/bin/alembic" ] && [ -f "$TARGET_DIR/alembic.ini" ]; then
    run_sudo -u nextlife bash -c "set -a; [ -f '$TARGET_DIR/.env' ] && . '$TARGET_DIR/.env'; '$VENV_DIR/bin/alembic' -c '$TARGET_DIR/alembic.ini' upgrade head"
    log "  Migrations applied successfully"
fi

# 5. Restart service
log "Step 5: Restarting $SERVICE_NAME and nextlife-worker"
if [ -f "$REPO_ROOT/systemd/nextlife-worker.service" ]; then
    run_sudo cp "$REPO_ROOT/systemd/nextlife-worker.service" /etc/systemd/system/
fi
run_sudo systemctl daemon-reload
run_sudo systemctl enable nextlife-worker.service || true
run_sudo systemctl restart "$SERVICE_NAME"
run_sudo systemctl restart nextlife-worker.service || true

# 6. Verify healthcheck
log "Step 6: Verifying service health at $HEALTH_URL"
HEALTH_OK=0
for i in $(seq 1 20); do
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || true)"
    if [ "$STATUS" = "200" ]; then
        HEALTH_OK=1
        break
    fi
    sleep 1
done

if [ "$HEALTH_OK" -ne 1 ]; then
    die "Health check failed after 20s (last HTTP status: $STATUS)"
fi

log "  Health check passed (HTTP 200)!"

# 7. Keep only last 3 backups
find "$BACKUP_BASE" -maxdepth 1 -type d -name "app_pre_*" | sort | head -n -3 | while read -r old_bak; do
    [ -n "$old_bak" ] && run_sudo rm -rf "$old_bak"
done

ROLLBACK_NEEDED=0
log "=== Deploy Succeeded ==="
