"""Tests for Google Gemini (Imagen 3) Graphic AI integration."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nextlife.api.main import create_app
from nextlife.media.ai_edit import (
    ImageAIError,
    ImageAINotConfiguredError,
    call_image_ai_edits,
    get_image_ai_config,
    is_image_ai_configured,
)


def _make_dummy_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (100, 100), color=(10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_gemini_config_and_is_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("nextlife.media.ai_edit.get_data_dir", lambda: tmp_path)
    monkeypatch.delenv("NEXTLIFE_IMAGE_AI_PROVIDER", raising=False)
    monkeypatch.delenv("NEXTLIFE_IMAGE_AI_ENDPOINT", raising=False)
    monkeypatch.delenv("NEXTLIFE_IMAGE_AI_KEY", raising=False)
    monkeypatch.delenv("NEXTLIFE_IMAGE_AI_MODEL", raising=False)

    # Initially empty
    assert not is_image_ai_configured()

    # Save Gemini config to settings.json
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({
            "image_ai": {
                "provider": "gemini",
                "api_key": "AIzaSyTestKey",
                "model": "imagen-3.0-generate-002",
            }
        }),
        encoding="utf-8",
    )

    cfg = get_image_ai_config()
    assert cfg["provider"] == "gemini"
    assert cfg["api_key"] == "AIzaSyTestKey"
    assert cfg["model"] == "imagen-3.0-generate-002"
    assert is_image_ai_configured()


def test_call_gemini_imagen_mocked():
    dummy_jpeg = _make_dummy_jpeg_bytes()
    b64_str = base64.b64encode(dummy_jpeg).decode("ascii")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "predictions": [
            {"bytesBase64Encoded": b64_str}
        ]
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        res = call_image_ai_edits(
            image_bytes=b"dummy_input",
            prompt="Studio shot of sneakers",
            config={
                "provider": "gemini",
                "endpoint": "",
                "api_key": "AIzaSySecret",
                "model": "imagen-3.0-generate-002",
            },
        )
        assert res == dummy_jpeg
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key=AIzaSySecret" == args[0]
        assert kwargs["headers"]["x-goog-api-key"] == "AIzaSySecret"
        assert kwargs["headers"]["Content-Type"] == "application/json"

        payload = kwargs["json"]
        assert payload["instances"][0]["prompt"] == "Studio shot of sneakers"
        assert payload["parameters"]["sampleCount"] == 1
        assert payload["parameters"]["aspectRatio"] == "1:1"
        assert payload["parameters"]["outputOptions"]["mimeType"] == "image/jpeg"
        assert payload["parameters"]["personGeneration"] == "ALLOW_ADULT"
        assert payload["parameters"]["safetySetting"] == "BLOCK_MEDIUM_AND_ABOVE"


def test_call_gemini_imagen_custom_endpoint_and_errors():
    dummy_jpeg = _make_dummy_jpeg_bytes()
    b64_str = base64.b64encode(dummy_jpeg).decode("ascii")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "predictions": [{"bytesBase64Encoded": b64_str}]
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        res = call_image_ai_edits(
            image_bytes=b"dummy",
            prompt="Luxury watch photography",
            config={
                "provider": "gemini",
                "endpoint": "https://custom-proxy.example.com/v1beta/models/custom-model:predict",
                "api_key": "AIzaSy123",
                "model": "custom-model",
            },
        )
        assert res == dummy_jpeg
        args, _ = mock_post.call_args
        assert args[0] == "https://custom-proxy.example.com/v1beta/models/custom-model:predict?key=AIzaSy123"

    # Test missing config raises ImageAINotConfiguredError
    with pytest.raises(ImageAINotConfiguredError):
        call_image_ai_edits(
            image_bytes=b"dummy",
            prompt="fail",
            config={"provider": "gemini", "endpoint": "", "api_key": ""},
        )

    # Test error status response
    err_resp = MagicMock()
    err_resp.status_code = 400
    err_resp.text = "INVALID_ARGUMENT: Bad API Key"
    with patch("httpx.post", return_value=err_resp):
        with pytest.raises(ImageAIError) as exc_info:
            call_image_ai_edits(
                image_bytes=b"dummy",
                prompt="fail",
                config={"provider": "gemini", "api_key": "bad-key"},
            )
        assert "400" in str(exc_info.value)


def test_settings_gemini_save_and_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nextlife.services.llm_filter.get_data_dir", lambda: tmp_path)

    from nextlife.api.routes import check_auth

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    # Save settings with Gemini provider
    resp = client.post(
        "/ui/settings/ai",
        data={
            "llm_endpoint": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
            "llm_model": "gpt-4o-mini",
            "image_ai_provider": "gemini",
            "image_ai_endpoint": "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict",
            "image_ai_api_key": "AIzaSyRealKey",
            "image_ai_model": "imagen-3.0-generate-002",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "tab=ai" in resp.headers["location"]

    # Verify settings persisted
    settings_file = tmp_path / "settings.json"
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["image_ai"]["provider"] == "gemini"
    assert saved["image_ai"]["api_key"] == "AIzaSyRealKey"
    assert saved["image_ai"]["model"] == "imagen-3.0-generate-002"

    # Test connection button
    test_resp = client.post("/ui/settings/test-image-ai", follow_redirects=False)
    assert test_resp.status_code == 303
    assert "notice=image_ai_test_ok" in test_resp.headers["location"]

    # Verify UI page renders with Gemini selected and controls present
    get_resp = client.get("/ui/settings?tab=ai")
    assert get_resp.status_code == 200
    assert 'value="gemini" selected' in get_resp.text
    assert 'data-testid="select-image-ai-provider"' in get_resp.text
    assert 'AIzaSyRealKey' in get_resp.text
