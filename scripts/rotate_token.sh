#!/usr/bin/env bash
# Rotate a NextLife token stored in the project-root .env.
#
# Usage: scripts/rotate_token.sh <VAR_NAME>
#   Allowed: NEXTLIFE_WEB_TOKEN, NEXTLIFE_OPENCLAW_TOKEN
#
# What it does:
#   1. Generates a fresh token via `secrets.token_urlsafe(32)`.
#   2. Replaces the <VAR_NAME> line in .env (creates the file if absent).
#   3. Restricts .env to owner read/write (chmod 600).
#
# It NEVER prints the token value to stdout (only a confirmation without the
# value). After rotating NEXTLIFE_OPENCLAW_TOKEN, the OpenClaw-side copy
# (skills.entries.nextlife.env in ~/.openclaw/openclaw.json) must be updated
# and the openclaw-gateway restarted; after rotating NEXTLIFE_WEB_TOKEN,
# restart the web service.
set -euo pipefail

ALLOWED_VARS="NEXTLIFE_WEB_TOKEN NEXTLIFE_OPENCLAW_TOKEN"

VAR_NAME="${1:-}"
if [ -z "$VAR_NAME" ]; then
  echo "Usage: scripts/rotate_token.sh <VAR_NAME>" >&2
  echo "Allowed: $ALLOWED_VARS" >&2
  exit 2
fi
case " $ALLOWED_VARS " in
  *" $VAR_NAME "*) ;;
  *)
    echo "rotate_token: variable '$VAR_NAME' is not allowed" >&2
    echo "Allowed: $ALLOWED_VARS" >&2
    exit 2
    ;;
esac

# Repository root = parent of this script's directory (scripts/ -> root).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

# 1) Generate a fresh token, kept in a shell variable only (never echoed).
NEW_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
if [ -z "$NEW_TOKEN" ]; then
  echo "rotate_token: failed to generate a token" >&2
  exit 1
fi

# 2) Drop any existing <VAR_NAME> line, then append the new one.
if [ -f "$ENV_FILE" ]; then
  sed -i "/^${VAR_NAME}=/d" "$ENV_FILE"
else
  : > "$ENV_FILE"
fi
printf '%s=%s\n' "$VAR_NAME" "$NEW_TOKEN" >> "$ENV_FILE"

# 3) Owner read/write only.
chmod 600 "$ENV_FILE"

# 4) Confirm without revealing the value.
echo "Rotated $VAR_NAME in $ENV_FILE (token length ${#NEW_TOKEN}, mode 600)."
