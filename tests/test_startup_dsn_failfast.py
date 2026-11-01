"""Item 0.3 / C-01: the server must fail fast at startup when NEXTLIFE_DSN is
missing, naming the variable — not start lazily and die on the first DB request.

Regression-network counterpart of selfcheck C-01 (which validates the
``migrate.py`` process). This test covers the real app factory, i.e. the
``uvicorn nextlife.api.main:create_app --factory`` production startup path.
"""

import pytest

from nextlife.core.exceptions import SecretResolutionError


def test_create_app_fails_fast_when_nextlife_dsn_missing(monkeypatch):
    """With NEXTLIFE_DSN unset, create_app() raises naming the variable."""
    monkeypatch.delenv("NEXTLIFE_DSN", raising=False)

    from nextlife.api.main import create_app

    with pytest.raises(SecretResolutionError) as excinfo:
        create_app()
    assert "NEXTLIFE_DSN" in str(excinfo.value)


def test_create_app_starts_when_nextlife_dsn_present():
    """Control: with NEXTLIFE_DSN set (the test DSN from conftest), the
    fail-fast does not fire and create_app() returns an app."""
    from nextlife.core.config import get_database_url, load_config

    # Sanity: under the test session the env carries a resolvable DSN.
    assert get_database_url(load_config())

    from nextlife.api.main import create_app

    app = create_app()
    assert app is not None
