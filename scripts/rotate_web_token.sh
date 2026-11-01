#!/usr/bin/env bash
# Rotate the NextLife web token stored in the project-root .env.
#
# Compatibility wrapper (item 3): the canonical implementation is
# scripts/rotate_token.sh NEXTLIFE_WEB_TOKEN. Kept so existing references
# (BEFORE_DEPLOY.MD, runbooks) continue to work.
#
# Usage: scripts/rotate_web_token.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rotate_token.sh" NEXTLIFE_WEB_TOKEN
