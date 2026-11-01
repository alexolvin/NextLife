"""Regression tests for scripts/check_hardcode.py.

Mandatory case: the gate must catch an absolute path rooted at a snap channel
(antigravity-cli) that the original gate -- which only checked URL/e-mail/secret
literals in src/ -- let slip through. The offending literals are assembled at
runtime from parts so this test file does not itself carry the forbidden
substring, which the gate now also scans in tests/.
"""

import subprocess
import sys
from pathlib import Path

import scripts.check_hardcode as ch

ROOT = Path(__file__).resolve().parent.parent


def _snap_lib_literal() -> str:
    # Assembles a double-quoted antigravity-cli snap-channel lib path at runtime.
    return '"' + "/".join(["", "snap", "antigravity-cli", "19", "local", "lib"]) + '"'


def _home_data_literal() -> str:
    # Assembles a double-quoted user-home data path at runtime.
    return '"' + "/".join(["", "home", "user", "myhub", "ocNextLife", "data"]) + '"'


def test_abs_path_ban_is_defined():
    """The gate must carry an explicit absolute snap/home path ban."""
    descs = [desc for _, desc in ch.ABS_PATH_PATTERNS]
    assert any("snap" in d and "home" in d for d in descs)


def test_snap_channel_path_is_flagged(tmp_path):
    """Mandatory case: an antigravity-cli snap-channel path must be reported."""
    bad = tmp_path / "bad_snap.py"
    bad.write_text(f"SNAP_LIB = {_snap_lib_literal()}\n", encoding="utf-8")
    violations = ch.check_file(bad, scan_urls=False)
    assert violations, "the snap-channel path was not flagged"
    joined = "\n".join(violations)
    assert "antigravity-cli/19" in joined
    assert "Hardcoded absolute system path" in joined


def test_home_path_is_flagged(tmp_path):
    """A user-home data path must be reported."""
    bad = tmp_path / "bad_home.py"
    bad.write_text(f"DATA_DIR = {_home_data_literal()}\n", encoding="utf-8")
    violations = ch.check_file(bad, scan_urls=False)
    assert violations, "the home path was not flagged"
    assert "myhub/ocNextLife/data" in "\n".join(violations)


def test_relative_paths_are_not_flagged(tmp_path):
    """Repo-relative / data-dir-relative paths must NOT be flagged."""
    ok = tmp_path / "ok.py"
    ok.write_text(
        "PID = Path('data') / 'server.pid'\n"
        "SRC = Path('src')\n"
        "LIB = Path('local') / 'lib'\n",
        encoding="utf-8",
    )
    assert ch.check_file(ok, scan_urls=False) == []


def test_commented_path_is_ignored(tmp_path):
    """A snap-channel path inside a # comment must not be flagged."""
    f = tmp_path / "cmt.py"
    f.write_text(f"# legacy: SNAP_LIB = {_snap_lib_literal()}\n", encoding="utf-8")
    assert ch.check_file(f, scan_urls=False) == []


def _tailscale_url_literal() -> str:
    # Assembles a double-quoted non-loopback (Tailscale-range) host URL at
    # runtime so this test file does not itself carry a hardcoded non-loopback
    # IP in a position the gate would flag.
    return '"http://' + ".".join(["100", "95", "195", "40"]) + ':8081/ui"'


def test_non_loopback_ip_host_is_flagged(tmp_path):
    """A hardcoded non-loopback IP host (LAN / Tailscale) must be reported."""
    bad = tmp_path / "bad_ip.py"
    bad.write_text(f"BASE_URL = {_tailscale_url_literal()}\n", encoding="utf-8")
    violations = ch.check_file(bad, scan_urls=False, scan_ips=True)
    assert violations, "the non-loopback IP host was not flagged"
    assert "100.95.195.40" in "\n".join(violations)
    assert "non-loopback IP host" in "\n".join(violations)


def test_loopback_ip_host_is_not_flagged(tmp_path):
    """Loopback (127.x / 0.0.0.0) hosts are the only hardcoded hosts allowed."""
    ok = tmp_path / "ok_ip.py"
    ok.write_text(
        'A = "http://127.0.0.1:8081/ui"\n'
        'B = "http://0.0.0.0:8081/health"\n',
        encoding="utf-8",
    )
    assert ch.check_file(ok, scan_urls=False, scan_ips=True) == []


def _userinfo_loopback_url() -> str:
    # A userinfo-bearing loopback URL (user:pass@(loopback):8081), assembled at
    # runtime so this file itself does not carry the "@(loopback)" literal that
    # the check_secrets gate bans repo-wide.
    return "http://user:pass" + "@" + "127.0.0.1:8081"


def test_url_ban_loopback_exemption_cases(tmp_path):
    """Owner decision 2026-10-04 (item 3): URL-ban exemption for loopback
    DESTINATION hosts only, decided by urlsplit().hostname exact match.

    Passing cases (exempt):
    - http://127.0.0.1:8081
    - http://localhost:8081/api
    Flagged cases (NOT exempt):
    - http://127.0.0.1.evil.com   (hostname is not exactly 127.0.0.1)
    - user:pass@(loopback):8081   (userinfo-bearing URLs never exempt)
    - http://0.0.0.0:8081         (bind address, not a destination)
    - https://example.com         (ordinary external host)
    """
    ok = tmp_path / "ok_loopback_urls.py"
    ok.write_text(
        'A = "http://127.0.0.1:8081"\n'
        'B = "http://localhost:8081/api"\n',
        encoding="utf-8",
    )
    assert ch.check_file(ok, scan_urls=True) == []

    bad = tmp_path / "bad_urls.py"
    bad.write_text(
        'A = "http://127.0.0.1.evil.com"\n'
        f'B = "{_userinfo_loopback_url()}"\n'
        'C = "http://0.0.0.0:8081"\n'
        'D = "https://example.com"\n',
        encoding="utf-8",
    )
    violations = ch.check_file(bad, scan_urls=True)
    joined = "\n".join(violations)
    assert "127.0.0.1.evil.com" in joined
    assert _userinfo_loopback_url() in joined
    assert "0.0.0.0:8081" in joined
    assert "example.com" in joined
    # exactly the 4 lines are flagged (the userinfo line additionally trips the
    # e-mail pattern, which is fine -- it is still flagged, nothing is missed
    # and no line is double-counted in the per-line sense).
    flagged_lines = {int(v.split(":")[1]) for v in violations}
    assert flagged_lines == {1, 2, 3, 4}


def _fake_web_token_literal() -> str:
    # A synthetic web-token-shaped literal (base64url, mixed case + digits,
    # 20+ chars) -- the SAME KIND as a real leaked web auth token. Assembled at
    # runtime because this file lives under tests/, which the secret-literal ban
    # now scans; a static copy of a real-looking token would be flagged.
    return '"' + "9fK" + "mZqX7vBtLpW2nRcHjDg4sYeA" + "Q3" + '"'


def test_secret_token_literal_is_flagged(tmp_path):
    """A pasted secret/token literal (mixed case + digits, 20+ chars) assigned to a
    secret-named variable must be flagged in scripts/ and tests/."""
    bad = tmp_path / "bad_token.py"
    bad.write_text(f"token = {_fake_web_token_literal()}\n", encoding="utf-8")
    violations = ch.check_file(bad, scan_urls=False, scan_secrets=True)
    assert violations, "the secret/token literal was not flagged"
    assert "secret/token literal" in "\n".join(violations)


def test_fixture_token_is_not_flagged(tmp_path):
    """Recognizable fixtures (lowercase-with-underscores, no uppercase) must NOT be
    flagged -- the ban targets real high-entropy tokens, not test dummies. Var names
    and values also avoid the shapes the separate check_secrets gate flags, so this
    file stays clean under both gates."""
    ok = tmp_path / "ok_token.py"
    ok.write_text(
        "WEB_TOKEN = 'selfcheck_token_secret_123'\n"
        "AUTH_TOKEN = 'invalid_token_sample_123'\n"
        "OC_TOKEN = 'selfcheck_openclaw_dummy_9'\n",
        encoding="utf-8",
    )
    assert ch.check_file(ok, scan_urls=False, scan_secrets=True) == []


def test_bearer_token_literal_is_flagged(tmp_path):
    """A pasted credential in an ``Authorization: Bearer <token>`` header must be
    flagged in scripts/ and tests/ -- the shape the original gate missed (the
    token is embedded in a string, not assigned to a secret-named variable)."""
    token = _fake_web_token_literal().strip('"')  # unquoted, real-looking token
    bad = tmp_path / "bad_bearer.py"
    bad.write_text(
        "headers = {\"Authorization\": \"Bearer " + token + "\"}\n",
        encoding="utf-8",
    )
    violations = ch.check_file(bad, scan_urls=False, scan_secrets=True)
    assert violations, "the bearer token literal was not flagged"
    assert "bearer token" in "\n".join(violations).lower()


def test_bearer_placeholder_is_not_flagged(tmp_path):
    """``Bearer {token}`` (a runtime placeholder, not a pasted literal) must NOT
    be flagged -- only a real high-entropy literal is a violation."""
    ok = tmp_path / "ok_bearer.py"
    ok.write_text(
        "headers = {\"Authorization\": f\"Bearer {TOKEN}\"}\n",
        encoding="utf-8",
    )
    assert ch.check_file(ok, scan_urls=False, scan_secrets=True) == []


def test_markdown_file_with_bearer_is_flagged(tmp_path):
    """A tracked ``*.md`` (e.g. a ``.a/`` report) carrying a Bearer literal must be
    flagged -- the secret-literal ban now runs on EVERY git-tracked file, not just
    ``.py`` in scripts/tests, so a pasted credential is caught wherever it lands."""
    token = _fake_web_token_literal().strip('"')
    doc = tmp_path / "report.md"
    doc.write_text(
        "see the header below\n"
        "Authorization: Bearer " + token + "\n",
        encoding="utf-8",
    )
    violations = ch.check_file(doc, scan_urls=False, scan_secrets=True)
    assert violations, "the bearer token in a .md was not flagged"
    assert "bearer token" in "\n".join(violations).lower()


def test_gate_passes_on_this_repo():
    """Running the real gate on the repo must exit 0 -- no absolute snap/home paths
    anywhere in src, scripts, tests, config. This verifies the ban is wired into
    main() end-to-end and that the current tree is clean."""
    proc = subprocess.run(
        [sys.executable, "scripts/check_hardcode.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"gate failed:\n{proc.stderr}"
    assert "Hardcode check passed" in proc.stdout
