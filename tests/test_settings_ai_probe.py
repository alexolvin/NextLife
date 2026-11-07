"""Unit tests for AI endpoint probes (LLM and Image AI)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.api.routes import check_auth
from nextlife.services.settings_probe import probe_image_ai_connection, probe_llm_connection


def test_probe_llm_connection_missing_endpoint():
    ok, msg = probe_llm_connection({"provider": "openai", "endpoint": ""})
    assert not ok
    assert "URL эндпоинта LLM не задан" in msg


def test_probe_llm_connection_timeout():
    with patch("httpx.post", side_effect=httpx.ConnectTimeout("timed out")):
        ok, msg = probe_llm_connection({
            "provider": "openai",
            "endpoint": "http://100.126.222.23:8004/v1",
            "api_key": "dummy",
            "model": "qwen3.8-27b",
        })
        assert not ok
        assert "таймаут" in msg.lower()


def test_probe_llm_connection_refused():
    with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")):
        ok, msg = probe_llm_connection({
            "provider": "openai",
            "endpoint": "http://127.0.0.1:9999/v1",
        })
        assert not ok
        assert "соединение отклонено" in msg.lower() or "недоступен" in msg.lower()


def test_probe_llm_connection_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.post", return_value=mock_resp):
        ok, msg = probe_llm_connection({
            "provider": "openai",
            "endpoint": "http://100.126.222.23:8004/v1",
            "api_key": "dummy",
            "model": "qwen3.8-27b",
        })
        assert ok
        assert msg == "llm_test_ok"


def test_probe_image_ai_cloudflare_valid():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True}
    with patch("httpx.get", return_value=mock_resp):
        ok, msg = probe_image_ai_connection({
            "provider": "cloudflare",
            "api_key": "valid_token",
        })
        assert ok
        assert msg == "image_ai_test_ok"


def test_probe_image_ai_cloudflare_invalid():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": False}
    with patch("httpx.get", return_value=mock_resp):
        ok, msg = probe_image_ai_connection({
            "provider": "cloudflare",
            "api_key": "invalid_token",
        })
        assert not ok
        assert "недействителен" in msg


def test_routes_settings_test_llm_error_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nextlife.services.llm_filter.get_data_dir", lambda: tmp_path)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({
            "llm": {
                "provider": "openai",
                "endpoint": "http://100.126.222.23:8004/v1",
                "api_key": "dummy",
                "model": "qwen3.8-27b",
            }
        }),
        encoding="utf-8",
    )

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    with patch("httpx.post", side_effect=httpx.ConnectTimeout("timed out")):
        resp = client.post("/ui/settings/test-llm", follow_redirects=False)
        assert resp.status_code == 303
        loc = resp.headers["location"]
        assert "err=" in loc
        assert "notice=llm_test_ok" not in loc
