"""Unit tests for the .env fallback in nextlife.core.secrets.resolve_secret.

Locks in the precedence contract: the process environment always wins over the
project-root .env file, .env is consulted only when the variable is absent from
the process environment, and the failure message names the variable when it is
set in neither. The .env path is monkeypatched to a temp file so the real
project-root .env (if any) is never read and no test state leaks.
"""

import pathlib

import pytest

import nextlife.core.secrets as secrets_mod
from nextlife.core.exceptions import SecretResolutionError

DOTENV_VALUE = "dotenv_dummy_value"
ENV_VALUE = "process_dummy_value"


def _write_env_file(tmp_path, lines: str):
    env_file = tmp_path / ".env"
    env_file.write_text(lines, encoding="utf-8")
    return env_file


def test_process_env_wins_over_env_file(monkeypatch, tmp_path):
    env_file = _write_env_file(tmp_path, f"NEXTLIFE_WEB_TOKEN={DOTENV_VALUE}\n")
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", env_file)
    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", ENV_VALUE)
    assert secrets_mod.resolve_secret("NEXTLIFE_WEB_TOKEN") == ENV_VALUE


def test_falls_back_to_env_file_when_not_in_process_env(monkeypatch, tmp_path):
    env_file = _write_env_file(tmp_path, f"NEXTLIFE_WEB_TOKEN={DOTENV_VALUE}\n")
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", env_file)
    monkeypatch.delenv("NEXTLIFE_WEB_TOKEN", raising=False)
    assert secrets_mod.resolve_secret("NEXTLIFE_WEB_TOKEN") == DOTENV_VALUE


def test_secretref_prefix_resolves_from_env_file(monkeypatch, tmp_path):
    env_file = _write_env_file(tmp_path, f"NEXTLIFE_WEB_TOKEN={DOTENV_VALUE}\n")
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", env_file)
    monkeypatch.delenv("NEXTLIFE_WEB_TOKEN", raising=False)
    assert secrets_mod.resolve_secret("SecretRef:NEXTLIFE_WEB_TOKEN") == DOTENV_VALUE


def test_quotes_and_export_prefix_are_stripped(monkeypatch, tmp_path):
    env_file = _write_env_file(
        tmp_path,
        f'# comment\n'
        f"export OTHER_TOKEN=\"quoted_value\"\n"
        f"NEXTLIFE_WEB_TOKEN='{DOTENV_VALUE}'\n",
    )
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", env_file)
    monkeypatch.delenv("NEXTLIFE_WEB_TOKEN", raising=False)
    parsed = secrets_mod._parse_env_file(env_file)
    assert parsed["NEXTLIFE_WEB_TOKEN"] == DOTENV_VALUE
    assert parsed["OTHER_TOKEN"] == "quoted_value"
    assert "comment" not in parsed


def test_raises_naming_var_when_in_neither(monkeypatch, tmp_path):
    env_file = _write_env_file(tmp_path, "# only a comment here\n")
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", env_file)
    monkeypatch.delenv("NEXTLIFE_WEB_TOKEN", raising=False)
    with pytest.raises(SecretResolutionError) as excinfo:
        secrets_mod.resolve_secret("NEXTLIFE_WEB_TOKEN")
    assert "NEXTLIFE_WEB_TOKEN" in str(excinfo.value)


def test_missing_env_file_is_not_an_error(monkeypatch, tmp_path):
    # Point at a path that does not exist; a process-env value still resolves.
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", tmp_path / "does_not_exist.env")
    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", ENV_VALUE)
    assert secrets_mod.resolve_secret("NEXTLIFE_WEB_TOKEN") == ENV_VALUE


def _raise_permission_error(*args, **kwargs):
    raise PermissionError(13, "Permission denied")


def test_unreadable_env_file_is_treated_as_absent(monkeypatch, tmp_path):
    # A .env that exists but cannot be read (e.g. another user's file ->
    # PermissionError) must NOT leak a traceback. resolve_secret treats it as
    # "no variables here" and raises SecretResolutionError naming the variable,
    # exactly as if the file were missing.
    env_file = _write_env_file(tmp_path, f"NEXTLIFE_WEB_TOKEN={DOTENV_VALUE}\n")
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", env_file)
    monkeypatch.delenv("NEXTLIFE_WEB_TOKEN", raising=False)
    monkeypatch.setattr(pathlib.Path, "read_text", _raise_permission_error)
    with pytest.raises(SecretResolutionError) as excinfo:
        secrets_mod.resolve_secret("NEXTLIFE_WEB_TOKEN")
    assert "NEXTLIFE_WEB_TOKEN" in str(excinfo.value)


def test_unreadable_env_file_does_not_block_process_env(monkeypatch, tmp_path):
    # Process env wins: even with an unreadable .env, a process-env value
    # resolves (the .env is only consulted when the var is absent from the env).
    env_file = _write_env_file(tmp_path, f"NEXTLIFE_WEB_TOKEN={DOTENV_VALUE}\n")
    monkeypatch.setattr(secrets_mod, "_ENV_FILE", env_file)
    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", ENV_VALUE)
    monkeypatch.setattr(pathlib.Path, "read_text", _raise_permission_error)
    assert secrets_mod.resolve_secret("NEXTLIFE_WEB_TOKEN") == ENV_VALUE
