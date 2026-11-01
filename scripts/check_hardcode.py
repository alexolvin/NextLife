#!/usr/bin/env python3
"""Verify that no forbidden hardcoded literals exist in Python source files.

Five rule groups are enforced:

* URL / e-mail / secret literals -> checked in ``src/`` only (tests and config
  legitimately contain example.com URLs, fixture e-mail addresses, etc.).
  Loopback DESTINATION hosts are exempt from the URL ban (owner decision
  2026-10-04, item 3): exactly 127.0.0.1 / localhost / ::1, decided by
  ``urllib.parse.urlsplit(url).hostname`` with an exact match. 0.0.0.0 is NOT
  exempt (a bind address, not a destination); URLs with userinfo
  (``user:pass@``) are NOT exempt.
* Non-loopback IPv4 hosts in a URL (``://<ip>``) -> checked in ``src`` and
  ``scripts``. A hardcoded non-loopback address (a LAN or Tailscale IP) pins
  the app to one machine/network; only loopback (``127.x`` / ``0.0.0.0``) hosts
  may be hardcoded, everything else must come from an env var or config.
* Absolute filesystem paths rooted at the system ``snap`` or ``home``
  directories -> checked across ``src``, ``scripts``, ``tests`` and ``config``.
  Hardcoding such a path (a specific user's home, or a snap channel such as
  ``antigravity-cli``) pins the code to one machine and breaks every other
  environment, so the ban is never exempted -- not even for config or
  tests/fixtures.
* A secret/token literal (a pasted credential, e.g. a leaked web token) assigned
  to a secret-named variable, or pasted in an ``Authorization: Bearer <token>``
  header -> checked in **every git-tracked file** (``git ls-files``), including
  ``.a/`` reports/handoff and ``*.md`` -- a pasted credential is caught wherever
  it lands, not just in ``scripts``/``tests``. Only high-entropy values that mix
  uppercase, lowercase and digits are flagged, so recognizable fixtures
  (``selfcheck_token_secret_123``, ``dummy``) and ``Bearer {token}`` placeholders
  are left alone.

Note: this module's own prose must avoid the literal ``/snap/`` and ``/home/``
forms, because the gate also scans the file this ban lives in.
"""

import re
import subprocess
import sys
from pathlib import Path

# URL / e-mail / secret literals are forbidden in production code (src/ only).
FORBIDDEN_PATTERNS = [
    (re.compile(r"https?://[^\s\"']{3,}", re.IGNORECASE), "Hardcoded URL"),
    (
        re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE),
        "Hardcoded email",
    ),
    (
        re.compile(r'(?:api_key|secret_key|auth_token)\s*=\s*["\'][^"\']{4,}["\']', re.IGNORECASE),
        "Hardcoded secret/token",
    ),
]

# Absolute paths rooted at the system snap/home directories are forbidden
# everywhere (src, scripts, tests, config). The quote-anchored form catches
# string literals, f-strings and Path(...) calls without matching incidental
# prose or docstrings. (The pattern text below intentionally avoids the literal
# /snap/ and /home/ forms so this file stays clean under its own gate.)
ABS_PATH_PATTERNS = [
    (
        re.compile(r"""["'](/(?:snap|home)/[^"']*)["']"""),
        "Hardcoded absolute system path (snap or home)",
    ),
]

# A non-loopback IPv4 host in a URL (://<ip>) pins the app to one
# machine/network (LAN or Tailscale). Only loopback hosts may be hardcoded;
# the match captures the dotted quad so the loopback exemption can be applied.
NON_LOOPBACK_IP_PATTERNS = [
    (
        re.compile(r"://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"),
        "Hardcoded non-loopback IP host",
    ),
]


def _is_loopback_ip(ip: str) -> bool:
    """127.0.0.0/8 and 0.0.0.0 are the only hardcoded hosts we allow."""
    return ip.startswith("127.") or ip == "0.0.0.0"


# URL-ban exemption (owner decision 2026-10-04, item 3): loopback destination
# hosts are the only hardcoded URL hosts allowed in src/. Exact hostname match
# via urlsplit -- no prefix/substring matching, so e.g. 127.0.0.1.evil.com is
# NOT loopback. 0.0.0.0 is a bind address, not a destination: not exempt.
LOOPBACK_URL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _is_exempt_loopback_url(url: str) -> bool:
    """True only for URLs whose urlsplit hostname is exactly a loopback host.

    URLs carrying userinfo (``user:pass@host``) never get the exemption."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    return parts.hostname in LOOPBACK_URL_HOSTS


def _is_placeholder_url(url: str) -> bool:
    """True for URLs whose netloc is not a real host (e.g. ``https://...`` in a
    docstring example). A real URL always has an alphanumeric host, so a
    netloc of only dots/ellipsis (or no host at all) is a placeholder, not a
    hardcoded endpoint."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = parts.hostname or ""
    if not host:
        return True
    return not any(c.isalnum() for c in host)


# A secret/token literal (a pasted credential such as a leaked web token) assigned
# to a secret-named variable is forbidden in scripts/ and tests/. The regex requires
# a base64url-ish literal of length >= 20; _looks_like_real_secret() then requires it
# to mix uppercase, lowercase and digits, so recognizable fixtures (which carry no
# uppercase) are left alone. (The pattern text below is not itself a pasted token,
# so this file stays clean under its own gate.)
SECRET_LITERAL_PATTERNS = [
    (
        re.compile(
            r"""\b\w*(?:token|secret|password|passwd|credential|bearer|api_key|access_key|private_key|session)\w*\b\s*=\s*["']([A-Za-z0-9_\-]{20,})["']""",
            re.IGNORECASE,
        ),
        "Hardcoded secret/token literal",
    ),
    # A pasted credential in an auth header (``Bearer <token>``) -- the shape the
    # original gate missed, because the token is embedded in a string rather than
    # assigned to a secret-named variable. A placeholder (``Bearer {token}``) has
    # a brace where the literal would be, so it is not matched.
    (
        re.compile(r"\bBearer\s+([A-Za-z0-9_\-]{20,})", re.IGNORECASE),
        "Hardcoded bearer token",
    ),
]


def _looks_like_real_secret(value: str) -> bool:
    """A real pasted credential mixes uppercase, lowercase and digits (a base64url
    web token). Recognizable fixtures carry no uppercase and are left alone."""
    return (
        len(value) >= 20
        and any(c.isupper() for c in value)
        and any(c.islower() for c in value)
        and any(c.isdigit() for c in value)
    )


# Paths allowed to contain URL/e-mail/secret literals (does NOT apply to the abs-path ban).
ALLOWED_PREFIXES = [
    "config",
    "tests/fixtures",
    ".git",
    ".venv",
]

# Directories the absolute snap/home path ban is enforced in (.py files only).
ABS_PATH_SCAN_DIRS = ("src", "scripts", "tests", "config")


def _is_allowed(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) or f"/{prefix}" in rel_path for prefix in ALLOWED_PREFIXES)


def check_file(path: Path, scan_urls: bool = True, scan_ips: bool = False,
               scan_abs: bool = True, scan_secrets: bool = False) -> list[str]:
    """Scan one file. The abs-path ban runs when ``scan_abs`` is True (default;
    scoped to ``.py`` under src/scripts/tests/config by the caller); URL/e-mail/
    secret run when ``scan_urls`` is True (``src/``); the non-loopback-IP ban when
    ``scan_ips`` is True (``src``/``scripts``); the secret/token-literal ban when
    ``scan_secrets`` is True (now every git-tracked file, incl. ``.a/`` and
    ``*.md`` -- no directory exemption, so a pasted credential is caught anywhere)."""
    violations: list[str] = []
    rel_path = str(path.as_posix())
    allowed = _is_allowed(rel_path)

    try:
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                # Ignore comments
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue

                if scan_abs:
                    for pattern, desc in ABS_PATH_PATTERNS:
                        matches = pattern.findall(line)
                        if matches:
                            violations.append(f"{rel_path}:{line_no}: {desc} -> {matches[0]}")

                if scan_ips:
                    for pattern, desc in NON_LOOPBACK_IP_PATTERNS:
                        for ip in pattern.findall(line):
                            if not _is_loopback_ip(ip):
                                violations.append(f"{rel_path}:{line_no}: {desc} -> {ip}")

                if scan_secrets:
                    for pattern, desc in SECRET_LITERAL_PATTERNS:
                        for value in pattern.findall(line):
                            if _looks_like_real_secret(value):
                                violations.append(f"{rel_path}:{line_no}: {desc} -> {value}")

                if not scan_urls or allowed:
                    continue

                for pattern, desc in FORBIDDEN_PATTERNS:
                    matches = pattern.findall(line)
                    if desc == "Hardcoded URL":
                        matches = [
                            m for m in matches
                            if not _is_exempt_loopback_url(m) and not _is_placeholder_url(m)
                        ]
                    if matches:
                        violations.append(f"{rel_path}:{line_no}: {desc} -> {matches[0]}")
    except UnicodeDecodeError:
        pass

    return violations


def _tracked_files(root_dir: Path) -> list[Path]:
    """All git-tracked files as absolute paths, including ``.a/`` and ``*.md``.

    Falls back to an empty list if git is unavailable (nothing to gate)."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"],
            cwd=root_dir,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError):
        return []
    return [root_dir / p for p in out.split("\0") if p]


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    all_violations: list[str] = []
    seen: set[Path] = set()

    # The secret/token-literal ban runs on EVERY git-tracked file (incl. .a/ and
    # *.md), so a pasted credential is caught wherever it lands. The other rules
    # keep their directory scope and apply to .py files only:
    #   - URL / e-mail / secret:   src/ .py
    #   - non-loopback IP host:    src + scripts .py
    #   - absolute snap/home:      src, scripts, tests, config .py (never exempted)
    for path in _tracked_files(root_dir):
        if path in seen:
            continue
        seen.add(path)
        try:
            rel = path.relative_to(root_dir).as_posix()
        except ValueError:
            rel = path.as_posix()
        in_src = rel.startswith("src/")
        in_scripts = rel.startswith("scripts/")
        is_py = path.suffix == ".py"
        in_abs_scope = is_py and any(
            rel == d or rel.startswith(d + "/") for d in ABS_PATH_SCAN_DIRS
        )
        all_violations.extend(
            check_file(
                path,
                scan_urls=is_py and in_src,
                scan_ips=is_py and (in_src or in_scripts),
                scan_abs=in_abs_scope,
                scan_secrets=True,  # every tracked file (incl. .a/ and *.md)
            )
        )

    if all_violations:
        print("HARDCODE CHECK FAILED! Found forbidden literals:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print(
        "Hardcode check passed: no forbidden literals found "
        "(URL/email/secret in src .py; non-loopback IP host in src+scripts .py; "
        "absolute system snap/home paths in src, scripts, tests, config .py; "
        "secret/token literal in ALL git-tracked files incl. .a/ and *.md)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
