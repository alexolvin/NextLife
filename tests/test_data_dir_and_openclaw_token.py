"""Item 2 (2.2 + 2.3): get_data_dir()/get_media_dir() centralize the data base,
and get_openclaw_token() resolves the NEXTLIFE_OPENCLAW_TOKEN SecretRef.

These lock in the deployment portability behavior: the data location follows
NEXTLIFE_DATA_DIR (default CWD ``data``) and the OpenClaw skill token is
sourced from the environment only, naming the variable when it is unset.
"""

from pathlib import Path

import pytest

from nextlife.core.exceptions import SecretResolutionError


def test_get_data_dir_defaults_to_cwd_data(monkeypatch):
    monkeypatch.delenv("NEXTLIFE_DATA_DIR", raising=False)
    from nextlife.core.config import get_data_dir

    assert get_data_dir() == Path("data")


def test_get_data_dir_follows_env(monkeypatch):
    monkeypatch.setenv("NEXTLIFE_DATA_DIR", "/opt/nextlife/data")
    from nextlife.core.config import get_data_dir

    assert get_data_dir() == Path("/opt/nextlife/data")


def test_get_data_dir_expands_user(monkeypatch):
    monkeypatch.setenv("NEXTLIFE_DATA_DIR", "~/nl_data")
    from nextlife.core.config import get_data_dir

    assert get_data_dir() == Path("~/nl_data").expanduser()


def test_get_media_dir_hangs_off_data_dir(monkeypatch):
    monkeypatch.setenv("NEXTLIFE_DATA_DIR", "/opt/nextlife/data")
    from nextlife.core.config import get_data_dir, get_media_dir

    assert get_media_dir() == get_data_dir() / "media"
    assert get_media_dir() == Path("/opt/nextlife/data/media")


def test_get_media_dir_default(monkeypatch):
    monkeypatch.delenv("NEXTLIFE_DATA_DIR", raising=False)
    from nextlife.core.config import get_media_dir

    assert get_media_dir() == Path("data/media")


def test_openclaw_token_secret_ref_declared_in_config():
    from nextlife.core.config import load_config

    assert load_config().app.auth.openclaw_token_secret_ref == "NEXTLIFE_OPENCLAW_TOKEN"


def test_get_openclaw_token_resolves_from_env(monkeypatch):
    monkeypatch.setenv("NEXTLIFE_OPENCLAW_TOKEN", "oc_secret_token_123")
    from nextlife.core.config import get_openclaw_token

    assert get_openclaw_token() == "oc_secret_token_123"


def test_get_openclaw_token_missing_names_var(monkeypatch, tmp_path):
    # Item 3: the server-side token legitimately lives in the project .env
    # (resolve_secret falls back to it). This test pins the NAMING behavior
    # for a variable that is absent from the process env AND from .env, so it
    # points the resolver at an empty .env stand-in.
    import nextlife.core.secrets as secrets_mod

    monkeypatch.delenv("NEXTLIFE_OPENCLAW_TOKEN", raising=False)
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", tmp_path / "nonexistent.env")
    from nextlife.core.config import get_openclaw_token

    with pytest.raises(SecretResolutionError) as excinfo:
        get_openclaw_token()
    assert "NEXTLIFE_OPENCLAW_TOKEN" in str(excinfo.value)
