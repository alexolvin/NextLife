#!/usr/bin/env python3
"""Gate script to detect hardcoded secrets and connection strings (Task K1-013)."""

import os
import re
import sys
from pathlib import Path

# Directories to scan per task K1-013
TARGET_DIRS = ["src", "scripts", "tests", "config"]

# Forbidden patterns per K1-013 requirements:
# 1. Connection strings of any DBMS
# 2. Hex strings of length >= 24 (excluding verified checksum fields)
# 3. password=
# 4. @127.0.0.1
# 5. @localhost
# 6. secret (hardcoded secret / secret=)
# 7. token= (hardcoded token value token=...)
PATTERNS = [
    (
        re.compile(r"(?:postgres(?:ql)?|mysql|mariadb|sqlite|mongodb|redis|oracle|mssql)://[^\s\"']+", re.IGNORECASE),
        "DBMS connection string",
    ),
    (
        re.compile(r"@(?:127\.0\.0\.1|localhost)\b", re.IGNORECASE),
        "Host endpoint @127.0.0.1 / @localhost",
    ),
    (
        # A boolean value (true/false, optionally quoted) is not a secret —
        # the Android uiautomator `password="false"` node attribute would
        # otherwise false-positive on every real device dump.
        re.compile(r"\bpassword\s*=\s*(?!(?:['\"])?(?:true|false)(?:['\"])?(?![a-zA-Z0-9]))[^\s,;)]+", re.IGNORECASE),
        "Hardcoded password pattern (password=)",
    ),
    (
        re.compile(r"\b(?:api_key|secret_key)\s*=\s*['\"][^'\"]+['\"]", re.IGNORECASE),
        "Hardcoded API key / secret key",
    ),
    (
        re.compile(r"(?<![a-zA-Z0-9_])secret\s*=\s*['\"][^'\"]+['\"]", re.IGNORECASE),
        "Hardcoded secret assignment (secret=)",
    ),
    (
        re.compile(r"(?<![a-zA-Z0-9_])token=[a-zA-Z0-9_+/.-]{8,}", re.IGNORECASE),
        "Hardcoded token value (token=...)",
    ),
    (
        re.compile(r"(?<!sha256=)(?<!hash=)(?<!sha=)['\"][0-9a-fA-F]{24,}['\"]"),
        "Hardcoded hex literal (length >= 24)",
    ),
    (
        re.compile(r"nextlife_secret_token_tailscale", re.IGNORECASE),
        "Forbidden default/predictable token literal",
    ),
]

IGNORED_FILES = {
    "check_secrets.py",
    "test_check_secrets.py",
}

IGNORED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".mypy_cache",
}


def check_line(line: str) -> list[str]:
    violations: list[str] = []
    stripped = line.strip()
    if stripped.startswith("#"):
        return violations

    # SecretRef references in YAML/Python are valid configuration references
    if "SecretRef:" in line:
        return violations

    for pattern, desc in PATTERNS:
        match = pattern.search(line)
        if match:
            violations.append(f"{desc}: '{match.group(0)}'")

    return violations


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    if path.name in IGNORED_FILES:
        return violations

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                matches = check_line(line)
                for m in matches:
                    violations.append(f"{path}:{line_no}: {m}")
    except Exception as exc:
        violations.append(f"{path}: could not read file: {exc}")

    return violations


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    all_violations: list[str] = []

    for dir_name in TARGET_DIRS:
        target_path = root_dir / dir_name
        if not target_path.exists():
            continue

        for root, dirs, files in os.walk(target_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for file in files:
                file_path = Path(root) / file
                all_violations.extend(check_file(file_path))

    if all_violations:
        print(f"SECRETS GATE FAILED! Found {len(all_violations)} violation(s):", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print("Secrets gate passed: no forbidden credentials found across src/, scripts/, tests/, config/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
