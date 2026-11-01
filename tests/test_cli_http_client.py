"""Item 3: the CLI is a thin HTTP client.

- fail-fast BEFORE any network call when NEXTLIFE_OPENCLAW_TOKEN is missing;
- NEXTLIFE_BASE_URL default + override;
- static guard: nextlife.cli.* never imports nextlife.storage.db / load_config;
- live-server integration (status ok, publish 409 -> exit 4).
"""

import json
from pathlib import Path

import pytest

from nextlife.cli import http_client
from nextlife.cli.main import handle_cli

CLI_DIR = Path(__file__).resolve().parent.parent / "src" / "nextlife" / "cli"


def test_missing_token_fails_before_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NEXTLIFE_OPENCLAW_TOKEN", raising=False)

    def _no_network(*args, **kwargs):
        raise AssertionError("network call attempted without a token")

    monkeypatch.setattr(http_client, "request", _no_network)

    code = handle_cli(["status"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert "NEXTLIFE_OPENCLAW_TOKEN" in data["error"]
    assert data["code"] == 2


def test_base_url_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXTLIFE_BASE_URL", raising=False)
    assert http_client.base_url() == "http://127.0.0.1:8081"
    monkeypatch.setenv("NEXTLIFE_BASE_URL", "http://127.0.0.1:9999")
    assert http_client.base_url() == "http://127.0.0.1:9999"


def test_get_token_reads_process_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTLIFE_OPENCLAW_TOKEN", "env_value")
    assert http_client.get_token() == "env_value"
    monkeypatch.setenv("NEXTLIFE_OPENCLAW_TOKEN", "   ")
    with pytest.raises(http_client.CliTokenMissingError):
        http_client.get_token()


def test_cli_sources_have_no_db_or_config_access() -> None:
    """Static guard: the CLI must stay a thin HTTP client (no DB, no config)."""
    for path in sorted(CLI_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "nextlife.storage.db" not in text, f"{path.name} imports the DB layer"
        assert "load_config" not in text, f"{path.name} loads server config"
        assert "get_db_session" not in text, f"{path.name} opens DB sessions"


def test_cli_status_against_live_server(
    live_server_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NEXTLIFE_BASE_URL", live_server_url)
    code = handle_cli(["status"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert "items_by_status" in data


def test_cli_publish_409_maps_to_exit4(
    live_server_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NEXTLIFE_BASE_URL", live_server_url)
    token = __import__("os").environ["NEXTLIFE_OPENCLAW_TOKEN"]

    _, created = http_client.request(
        "POST", "/api/oc/items", token=token,
        json_body={"title": "Exit4 Item", "category": "electronics", "price": 10, "currency": "CZK"},
    )
    code = created["code"]  # ТЗ «Склад» §5.2.1: full stock card
    http_client.request("POST", f"/api/oc/items/{code}/draft", token=token)

    exit_code = handle_cli(["publish", "--code", code])
    assert exit_code == 4
    data = json.loads(capsys.readouterr().out)
    assert data["code"] == 4
