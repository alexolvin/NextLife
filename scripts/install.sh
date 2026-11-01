#!/usr/bin/env bash
# NextLife installer — run as root: sudo make install
# Supports --dry-run: prints system-modifying commands instead of executing.
# Idempotent: safe to re-run after any partial failure.
#
# Layout (matches the existing install):
#   /opt/nextlife/app    — code (rsync from the repo)
#   /opt/nextlife/venv   — virtualenv
#   /var/lib/nextlife    — data dir (get_data_dir / NEXTLIFE_DATA_DIR), also the
#                          home of the system user `nextlife`
#   /etc/nftables/nextlife.nft — nft rules (port 8081 from lo + tailscale only)
set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
fi

# Log (real run only; dry-run prints to stdout)
if [ "$DRY_RUN" -eq 0 ]; then
    if [ -w /tmp/nextlife-install.log ] || [ ! -e /tmp/nextlife-install.log ]; then
        install -m 644 /dev/null /tmp/nextlife-install.log 2>/dev/null || true
    fi
    exec > >(tee -a /tmp/nextlife-install.log) 2>&1
fi

# Step tracking + error trap (rollback line)
CURRENT_STEP=""
trap 'rc=$?; if [ $rc -ne 0 ]; then echo ""; echo "Установка прервана на шаге ${CURRENT_STEP}. Откат: sudo make uninstall"; exit $rc; fi' EXIT

set_step() { CURRENT_STEP="$1"; }

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

stop_unit() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[DRY-RUN] systemctl stop $1"
    else
        systemctl stop "$1" 2>/dev/null || true
    fi
}
disable_unit() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[DRY-RUN] systemctl disable $1"
    else
        systemctl disable "$1" 2>/dev/null || true
    fi
}

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NEXTLIFE_USER="nextlife"
OPT_DIR="/opt/nextlife"
CODE_DIR="$OPT_DIR/app"
VENV_DIR="$OPT_DIR/venv"
DATA_DIR="/var/lib/nextlife"
BACKUP_DIR="/var/backups/nextlife"
REPO_DATA_DIR="$REPO_ROOT/data"
ENV_FILE="$REPO_ROOT/.env"
DSN="postgresql+psycopg2:///nextlife?host=/var/run/postgresql"
PORT=8081
DB_NAME="nextlife"

OWNER_USER="${SUDO_USER:-}"
if [ -z "$OWNER_USER" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        OWNER_USER="$(id -un)"
    else
        echo "[install] ERROR: cannot determine owner user — run via sudo" >&2
        exit 1
    fi
fi

log() { echo "[install] $*"; }
die() { echo "[install] ERROR: $*" >&2; exit 1; }

# Tailscale interface (for the nft allow-list); default tailscale0
TS_IFACE=""
if [ "$DRY_RUN" -eq 0 ]; then
    TS_IFACE="$(ip -o link show 2>/dev/null | grep -oE 'tailscale[0-9]+' | head -1 || true)"
fi
[ -n "$TS_IFACE" ] || TS_IFACE="tailscale0"

if [ "$DRY_RUN" -eq 1 ]; then
    log "=== DRY RUN MODE (no system changes) ==="
fi

# ═══════════════════════════════════════════════════════════════════════════
# PREFLIGHT (both dry-run and real mode, before any system change)
# ═══════════════════════════════════════════════════════════════════════════
set_step "preflight"
log "Preflight checks:"
printf "%-42s %-22s %s\n" "CHECK" "STATE" "ACTION"
printf "%-42s %-22s %s\n" "-----" "-----" "------"

# 1. System user
if getent passwd "$NEXTLIFE_USER" &>/dev/null; then
    printf "%-42s %-22s %s\n" "user $NEXTLIFE_USER" "exists" "skip"
else
    printf "%-42s %-22s %s\n" "user $NEXTLIFE_USER" "absent" "create"
fi

# 2. Port free (or used by our service)
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    if systemctl is-active nextlife-web &>/dev/null; then
        printf "%-42s %-22s %s\n" "port $PORT" "nextlife-web" "will restart"
    else
        printf "%-42s %-22s %s\n" "port $PORT" "IN USE (foreign)" "STOP"
        die "Port $PORT is in use by a non-nextlife process. Stop it first."
    fi
else
    printf "%-42s %-22s %s\n" "port $PORT" "free" "ok"
fi

# 3. Tailscale interface
printf "%-42s %-22s %s\n" "tailscale iface" "$TS_IFACE" "ok"

# 4. DB peer access (root-only; verified in real mode)
if [ "$DRY_RUN" -eq 1 ]; then
    printf "%-42s %-22s %s\n" "db peer (sudo -u nextlife psql)" "dry-run" "would verify"
else
    if sudo -u "$NEXTLIFE_USER" psql -d "$DB_NAME" -c 'select 1' &>/dev/null; then
        printf "%-42s %-22s %s\n" "db peer (sudo -u nextlife psql)" "ok" "ok"
    else
        printf "%-42s %-22s %s\n" "db peer (sudo -u nextlife psql)" "FAILED" "STOP"
        die "Peer access to the $DB_NAME DB failed: 'sudo -u $NEXTLIFE_USER psql -d $DB_NAME -c \"select 1\"'. pg_hba is NOT touched by this install — add a 'local $DB_NAME $NEXTLIFE_USER peer' rule and re-run."
    fi
fi

# 5. Env file (single secret source)
if [ -f "$ENV_FILE" ]; then
    printf "%-42s %-22s %s\n" "env file $ENV_FILE" "exists" "ok"
else
    printf "%-42s %-22s %s\n" "env file $ENV_FILE" "MISSING" "STOP"
    die "Env file $ENV_FILE not found — it is the single secret source."
fi

# 6. Data dir state
DATA_EMPTY=1
for sub in media deals; do
    if [ -d "$DATA_DIR/$sub" ] && [ -n "$(ls -A "$DATA_DIR/$sub" 2>/dev/null)" ]; then
        DATA_EMPTY=0
        break
    fi
done
if [ "$DATA_EMPTY" -eq 1 ]; then
    printf "%-42s %-22s %s\n" "data dir $DATA_DIR" "empty/absent" "copy from repo data/"
else
    printf "%-42s %-22s %s\n" "data dir $DATA_DIR" "not empty" "keep (no copy)"
fi
log ""

# ─── 0. Stop existing services ─────────────────────────────────────────────
set_step "0-stop"
log "Step 0: stop existing services"
stop_unit nextlife-web
stop_unit nextlife-nft
# Old dev unit (if ever installed) — free port 8081
stop_unit nextlife-api
disable_unit nextlife-api

# ─── 1. User + group ───────────────────────────────────────────────────────
set_step "1-user"
log "Step 1: user + group"
if ! getent group "$NEXTLIFE_USER" &>/dev/null; then
    run groupadd "$NEXTLIFE_USER"
fi
if ! getent passwd "$NEXTLIFE_USER" &>/dev/null; then
    run useradd -r -g "$NEXTLIFE_USER" -d "$DATA_DIR" -s /usr/sbin/nologin "$NEXTLIFE_USER"
fi

# ─── 2. Data directory ─────────────────────────────────────────────────────
set_step "2-datadir"
log "Step 2: data directory"
run mkdir -p "$DATA_DIR"
if [ "$DATA_EMPTY" -eq 1 ]; then
    log "  Data dir empty — copying from $REPO_DATA_DIR (source kept)"
    SRC_SIZE=$(du -sh "$REPO_DATA_DIR" 2>/dev/null | cut -f1 || echo "?")
    SRC_COUNT=$(find "$REPO_DATA_DIR" -type f 2>/dev/null | wc -l)
    run rsync -a "$REPO_DATA_DIR/" "$DATA_DIR/"
    log "  Copied source: $SRC_SIZE / $SRC_COUNT files"
fi
run chown -R "$NEXTLIFE_USER:$NEXTLIFE_USER" "$DATA_DIR"
run chmod 750 "$DATA_DIR"

# ─── 3. pg_dump backup (before any schema change) ──────────────────────────
set_step "3-backup"
log "Step 3: pg_dump backup"
run mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/$DB_NAME-$(date +%Y%m%d-%H%M%S).sql.gz"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY-RUN] sudo -u $NEXTLIFE_USER pg_dump $DB_NAME | gzip > $BACKUP_FILE"
else
    sudo -u "$NEXTLIFE_USER" pg_dump "$DB_NAME" | gzip > "$BACKUP_FILE"
    chown "$NEXTLIFE_USER:$NEXTLIFE_USER" "$BACKUP_FILE"
    BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
    log "  Backup: $BACKUP_FILE ($BACKUP_SIZE)"
fi

# ─── 4. Code + venv ────────────────────────────────────────────────────────
set_step "4-code"
log "Step 4: deploy code + venv"
run mkdir -p "$CODE_DIR"
run rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude 'venv' \
    --exclude '__pycache__' --exclude '.pytest_cache' \
    --exclude '.mypy_cache' --exclude '.ruff_cache' \
    --exclude '.a' --exclude 'data' --exclude '.env' \
    "$REPO_ROOT/" "$CODE_DIR/"
if [ "$DRY_RUN" -eq 1 ] || [ ! -d "$VENV_DIR" ]; then
    run python3 -m venv "$VENV_DIR"
fi
run "$VENV_DIR/bin/pip" install -e "$CODE_DIR" --quiet
run chown -R "$NEXTLIFE_USER:$NEXTLIFE_USER" "$OPT_DIR"

# build-info for /healthz (deploy integrity: commit must match git HEAD)
if [ "$DRY_RUN" -eq 0 ]; then
    GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo 'unknown')"
    BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '{"commit": "%s", "built_at": "%s"}\n' "$GIT_COMMIT" "$BUILT_AT" > "$DATA_DIR/build-info.json"
    chown "$NEXTLIFE_USER:$NEXTLIFE_USER" "$DATA_DIR/build-info.json"
    log "  build-info: commit=$GIT_COMMIT built_at=$BUILT_AT"
else
    echo "[DRY-RUN] write $DATA_DIR/build-info.json (commit + built_at)"
fi

# ─── 5. Migrations ─────────────────────────────────────────────────────────
set_step "5-migrate"
log "Step 5: database migrations"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY-RUN] sudo -u $NEXTLIFE_USER env NEXTLIFE_DSN=$DSN $VENV_DIR/bin/python $CODE_DIR/scripts/migrate.py"
else
    # env var is set INSIDE sudo (via `env`), not before it: sudo's env_reset
    # strips vars set in the calling environment, so `VAR=… sudo …` would lose
    # NEXTLIFE_DSN before migrate.py sees it.
    sudo -u "$NEXTLIFE_USER" env NEXTLIFE_DSN="$DSN" "$VENV_DIR/bin/python" "$CODE_DIR/scripts/migrate.py"
    log "  Migrations applied (head)"
fi

# ─── 6. nft rules + systemd units ──────────────────────────────────────────
set_step "6-systemd"
log "Step 6: nft rules + systemd units"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY-RUN] write /etc/nftables/nextlife.nft (port $PORT, ifaces lo + $TS_IFACE)"
else
    mkdir -p /etc/nftables
    cat > /etc/nftables/nextlife.nft <<NFT
table inet nextlife {
    chain input {
        type filter hook input priority filter; policy accept;
        iifname "lo" tcp dport $PORT accept
        iifname "$TS_IFACE" tcp dport $PORT accept
        tcp dport $PORT drop
    }
}
NFT
fi
run cp "$REPO_ROOT/systemd/nextlife-nft.service" /etc/systemd/system/
run cp "$REPO_ROOT/systemd/nextlife-web.service" /etc/systemd/system/
run systemctl daemon-reload
run systemctl enable nextlife-nft.service
run systemctl enable nextlife-web.service

# ─── 7. Start ──────────────────────────────────────────────────────────────
set_step "7-start"
log "Step 7: start services"
run systemctl start nextlife-nft
run systemctl start nextlife-web
if [ "$DRY_RUN" -eq 0 ]; then
    sleep 3
fi

# ─── 8. Post-checks ────────────────────────────────────────────────────────
set_step "done"
if [ "$DRY_RUN" -eq 0 ]; then
    log "Post-checks:"
    ACTIVE="$(systemctl is-active nextlife-web)"
    log "  nextlife-web: $ACTIVE"
    [ "$ACTIVE" = "active" ] || die "nextlife-web is not active ($ACTIVE)"
    # Retry: the service may take a few seconds to accept connections after
    # systemd reports it active. Up to 15 attempts x 2s = 30s.
    HEALTH=""
    for i in $(seq 1 15); do
        HEALTH="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)"
        [ "$HEALTH" = "200" ] && break
        sleep 2
    done
    log "  GET /health: $HEALTH (after $i attempts)"
    [ "$HEALTH" = "200" ] || die "GET /health returned $HEALTH (expected 200) after retries"
    LOT_COUNT="$(sudo -u "$NEXTLIFE_USER" psql -d "$DB_NAME" -tAc 'select count(*) from lot')"
    log "  lots in $DB_NAME DB: $LOT_COUNT"
fi

log "Install complete."
log "  Owner UI (from tailnet): http://vzu5-claw:$PORT/"
log "  CLI (OpenClaw):         http://127.0.0.1:$PORT/"
log "  Log: /tmp/nextlife-install.log"
