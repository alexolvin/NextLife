#!/usr/bin/env python3
"""Interactive secrets setup (V1.4).

Prompts the owner for marketplace/seller secrets and writes them to .env
(permissions 600). Existing values are shown masked and kept unless the owner
types a new one.

Keys: BAZOS_PHONE, SELLER_NAME, SELLER_PSC, AUKRO_SELLER_LOGIN, FEED_TOKEN.
Aukro publish is via the FEED channel (XML feed pulled by Manažer prodeje), so
Aukro login/password and the API key are no longer used (removed 2026-10-24).
FEED_TOKEN (32 random bytes, hex) is generated here, not typed.
The seller email in listings comes from the existing OpenClaw config (not here).
"""

from __future__ import annotations

import os
import re
import secrets as _secrets
import sys
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
# Typed keys (prompted from the owner). FEED_TOKEN is generated, not typed.
KEYS = ["BAZOS_PHONE", "SELLER_NAME", "SELLER_PSC", "AUKRO_SELLER_LOGIN"]


def parse_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> None:
    global ENV_FILE
    if len(sys.argv) > 1:
        ENV_FILE = Path(sys.argv[1])
    if not ENV_FILE.exists():
        raise SystemExit(f"ERROR: {ENV_FILE} not found")
    env = parse_env(ENV_FILE)
    for key in KEYS:
        cur = env.get(key, "")
        if cur:
            prompt = f"{key} (current: {cur[:3]}***) [Enter=keep]: "
        else:
            prompt = f"{key}: "
        val = input(prompt)
        if not val:
            if not cur:
                raise SystemExit(f"ERROR: {key} is required")
            print(f"  kept {key}")
            continue
        env[key] = val
        print(f"  set {key}")
    # FEED_TOKEN: 32 random bytes, hex (C2). Generated once, kept thereafter.
    if not env.get("FEED_TOKEN"):
        env["FEED_TOKEN"] = _secrets.token_hex(32)
        print("  generated FEED_TOKEN")
    else:
        print("  kept FEED_TOKEN")
    # Write back: drop old lines for the managed keys, append fresh ones.
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    for key in [*KEYS, "FEED_TOKEN"]:
        if key in env:
            lines = [ln for ln in lines if not re.match(rf"^{key}=", ln.strip())]
            lines.append(f"{key}={env[key]}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    print(f"Done. Secrets written to {ENV_FILE} (600).")


if __name__ == "__main__":
    main()
