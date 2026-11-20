#!/usr/bin/env bash
set -euo pipefail

# NextLife UI Rollback Script
# Reverts templates and static assets to pre-ui-redesign-2026-11-20

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG_NAME="pre-ui-redesign-2026-11-20"
TEMPLATES_DIR="templates"
STATIC_DIR="src/nextlife/api/static"
SERVICE_NAME="nextlife-web.service"

RESTART=1
for arg in "$@"; do
    if [ "$arg" == "--no-restart" ]; then
        RESTART=0
    fi
done

cd "$REPO_ROOT"

# Restore templates and static directories from tag
git checkout "$TAG_NAME" -- "$TEMPLATES_DIR" "$STATIC_DIR"

# Stage and commit if there are differences
if ! git diff --quiet --cached || ! git diff --quiet; then
    git add "$TEMPLATES_DIR" "$STATIC_DIR"
    git commit -m "revert: UI rollback to pre-ui-redesign" --allow-empty
else
    # In case working tree is already clean, create empty commit or skip
    git commit -m "revert: UI rollback to pre-ui-redesign" --allow-empty
fi

COMMIT_HASH="$(git rev-parse HEAD)"

# Restart service if requested
if [ "$RESTART" -eq 1 ]; then
    if [ -f "$REPO_ROOT/scripts/deploy.sh" ]; then
        bash "$REPO_ROOT/scripts/deploy.sh" >/dev/null 2>&1 || sudo systemctl restart "$SERVICE_NAME"
    else
        sudo systemctl restart "$SERVICE_NAME"
    fi
fi

echo "ROLLBACK OK $COMMIT_HASH"
