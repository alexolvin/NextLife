"""Tests for test database guard enforcement (Rule C-4, Task K1-012)."""

import os
import subprocess
import sys
import pytest
from sqlalchemy import text
from nextlife.storage.db import get_db_session


def test_guard_allows_test_database():
    """Verify that under normal test run, the connected database ends with _test."""
    with get_db_session() as session:
        result = session.execute(text("SELECT current_database();")).scalar()
        assert result is not None
        assert str(result).endswith("_test"), f"Expected test database ending with _test, got: {result}"


def test_guard_blocks_non_test_database():
    """Verify that running tests against a database without _test suffix is blocked.

    The child pytest process receives a NEXTLIFE_DSN pointing at the same
    database without the _test suffix. Depending on the role's privileges
    on that database the child aborts either at the connection step or at
    the explicit _test-suffix guard — in both cases before any write.
    """
    test_dsn = os.environ.get("NEXTLIFE_DSN")
    if not test_dsn:
        pytest.fail("NEXTLIFE_DSN is required to run this test")

    # Replace ONLY the db path component with a non-test name; never touch
    # the user-info part, otherwise credentials break.
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(test_dsn)
    db_name = parts.path.lstrip("/")
    if db_name.endswith("_test"):
        bad_db = db_name[: -len("_test")]
    else:
        bad_db = "non_test_db"
    bad_dsn = urlunsplit(parts._replace(path=f"/{bad_db}"))

    env = os.environ.copy()
    env["NEXTLIFE_DSN"] = bad_dsn

    # Run pytest on a single minimal test
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_db_guard.py::test_guard_allows_test_database",
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert (
        "Test database guard refusal" in out
        or "Failed to connect to test database" in out
    ), f"Expected guard refusal or connection failure, got: {out[-2000:]}"
