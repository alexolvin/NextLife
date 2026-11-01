"""Tests for check_secrets.py gate (Task K1-013)."""

import subprocess
import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
GATE_SCRIPT = ROOT_DIR / "scripts" / "check_secrets.py"
LEAK_STRING = "postgres" + "://" + "u:p@127.0.0.1:5432/db\n"


def run_gate() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
    )


def test_gate_passes_clean_repo():
    """Verify that current clean repository passes check_secrets.py without violations."""
    res = run_gate()
    assert res.returncode == 0, f"Expected gate to pass, got:\n{res.stdout}\n{res.stderr}"
    assert "Secrets gate passed" in res.stdout


@pytest.mark.parametrize("target_dir,filename", [
    ("src", "test_injected_leak.py"),
    ("scripts", "test_injected_leak.py"),
    ("tests", "test_injected_leak.py"),
    ("config", "test_injected_leak.yaml"),
])
def test_gate_fails_on_injected_secret_in_each_target_dir(target_dir: str, filename: str):
    """Verify gate fails when connection string is placed in each of 4 dirs (K1-013)."""
    target_file = ROOT_DIR / target_dir / filename
    try:
        target_file.write_text(LEAK_STRING, encoding="utf-8")
        res = run_gate()
        assert res.returncode != 0, f"Gate failed to detect leak in {target_dir}!"
        combined = res.stdout + res.stderr
        assert "SECRETS GATE FAILED" in combined
        assert str(target_file) in combined
        assert "u:p@127.0.0.1:5432/db" in combined
    finally:
        if target_file.exists():
            target_file.unlink()


def test_gate_regression_old_gate_blindspots():
    """Verify gate catches leaks in scripts/ and tests/ which the old gate missed."""
    for test_dir in ["scripts", "tests"]:
        leak_file = ROOT_DIR / test_dir / "old_gate_leak_regression.py"
        try:
            leak_file.write_text("DATABASE_URL = 'postgres" + "://" + "old_user:pass@127.0.0.1:5432/old_db'\n")
            res = run_gate()
            assert res.returncode != 0
            assert "SECRETS GATE FAILED" in (res.stdout + res.stderr)
        finally:
            if leak_file.exists():
                leak_file.unlink()


@pytest.mark.parametrize("pattern_line,desc", [
    ("mysql://admin:secret@localhost:3306/db", "DBMS connection string"),
    ("host = 'user@localhost'", "@localhost"),
    ("password = 'super_secret_password'", "password="),
    ("secret = 'top_secret_auth_token'", "secret="),
    ("token=super_secret_auth_token_value_123", "token="),
    ("token = 'ab1c6486461f2e29cde3c885'", "hex length >= 24"),
])
def test_gate_detects_individual_patterns(pattern_line: str, desc: str):
    """Verify each required pattern is recognized and flagged."""
    test_file = ROOT_DIR / "src" / "pattern_leak_test.py"
    try:
        test_file.write_text(f"val = '{pattern_line}'\n")
        res = run_gate()
        assert res.returncode != 0, f"Failed to detect pattern: {desc}"
    finally:
        if test_file.exists():
            test_file.unlink()
