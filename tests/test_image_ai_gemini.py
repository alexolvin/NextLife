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
    from nextlife.services.llm_filter import call_llm, is_llm_configured

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    # Save settings with Gemini provider for both Text LLM and Image AI
    resp = client.post(
        "/ui/settings/ai",
        data={
            "llm_provider": "gemini",
            "llm_endpoint": "",
            "llm_api_key": "AIzaSyLlmKey",
            "llm_model": "gemini-1.5-flash",
            "image_ai_provider": "gemini",
            "image_ai_endpoint": "",
            "image_ai_api_key": "AIzaSyImageKey",
            "image_ai_model": "imagen-3.0-generate-002",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "tab=ai" in resp.headers["location"]

    # Verify settings persisted
    settings_file = tmp_path / "settings.json"
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["llm"]["provider"] == "gemini"
    assert saved["llm"]["api_key"] == "AIzaSyLlmKey"
    assert saved["llm"]["model"] == "gemini-1.5-flash"
    assert saved["image_ai"]["provider"] == "gemini"
    assert saved["image_ai"]["api_key"] == "AIzaSyImageKey"
    assert saved["image_ai"]["model"] == "imagen-3.0-generate-002"

    assert is_llm_configured(saved["llm"])

    # Test connection buttons for both
    test_llm_resp = client.post("/ui/settings/test-llm", follow_redirects=False)
    assert test_llm_resp.status_code == 303
    assert "notice=llm_test_ok" in test_llm_resp.headers["location"]

    test_img_resp = client.post("/ui/settings/test-image-ai", follow_redirects=False)
    assert test_img_resp.status_code == 303
    assert "notice=image_ai_test_ok" in test_img_resp.headers["location"]

    # Verify UI page renders with both Gemini selected and controls present
    get_resp = client.get("/ui/settings?tab=ai")
    assert get_resp.status_code == 200
    assert 'data-testid="select-llm-provider"' in get_resp.text
    assert 'data-testid="select-image-ai-provider"' in get_resp.text
    assert 'AIzaSyLlmKey' in get_resp.text
    assert 'AIzaSyImageKey' in get_resp.text


def test_gemini_text_llm_call_mocked():
    from nextlife.services.llm_filter import call_llm

    fake_resp_data = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": '{"categories": ["GPU"], "assignments": ["GPU"]}'}]
                }
            }
        ]
    }
    fake_body = json.dumps(fake_resp_data).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_body
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = call_llm(
            endpoint="",
            api_key="AIzaSySecretLlm",
            model="gemini-1.5-flash",
            prompt="Classify this",
            provider="gemini",
        )
        assert res == '{"categories": ["GPU"], "assignments": ["GPU"]}'
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=AIzaSySecretLlm" == req.full_url
        assert req.get_header("Content-type") == "application/json"
        assert req.get_header("X-goog-api-key") == "AIzaSySecretLlm"
        body_obj = json.loads(req.data.decode("utf-8"))
        assert body_obj["contents"][0]["parts"][0]["text"] == "Classify this"


def test_gemini_generate_content_image_mocked():
    dummy_jpeg = _make_dummy_jpeg_bytes()
    b64_str = base64.b64encode(dummy_jpeg).decode("ascii")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": "image/jpeg", "data": b64_str}}
                    ]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        res = call_image_ai_edits(
            image_bytes=b"raw_input_image",
            prompt="Remove dust and scratches",
            config={
                "provider": "gemini",
                "endpoint": "",
                "api_key": "AIzaSySecret",
                "model": "gemini-2.5-flash-image",
            },
        )
        assert res == dummy_jpeg
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key=AIzaSySecret" == args[0]
        payload = kwargs["json"]
        parts = payload["contents"][0]["parts"]
        assert len(parts) == 2
        assert parts[0]["inlineData"]["mimeType"] == "image/png"
        assert parts[1]["text"] == "Remove dust and scratches"
        assert payload["generationConfig"]["responseModalities"] == ["IMAGE", "TEXT"]


def test_gemini_imagen_404_fallback_to_generate_content():
    dummy_jpeg = _make_dummy_jpeg_bytes()
    b64_str = base64.b64encode(dummy_jpeg).decode("ascii")

    err_404_resp = MagicMock()
    err_404_resp.status_code = 404
    err_404_resp.text = '{"error": {"code": 404, "message": "models/imagen-3.0-generate-002 is not found for API version v1beta, or is not supported for predict."}}'

    success_resp = MagicMock()
    success_resp.status_code = 200
    success_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": "image/jpeg", "data": b64_str}}
                    ]
                }
            }
        ]
    }

    with patch("httpx.post", side_effect=[err_404_resp, success_resp]) as mock_post:
        res = call_image_ai_edits(
            image_bytes=b"raw_input_image",
            prompt="Clean dust",
            config={
                "provider": "gemini",
                "endpoint": "",
                "api_key": "AIzaSySecret",
                "model": "imagen-3.0-generate-002",
            },
        )
        assert res == dummy_jpeg
        assert mock_post.call_count == 2
        # First call was predict on imagen
        first_url = mock_post.call_args_list[0][0][0]
        assert "imagen-3.0-generate-002:predict" in first_url
        # Second call was fallback to gemini-2.5-flash-image:generateContent
        second_url = mock_post.call_args_list[1][0][0]
        assert "gemini-2.5-flash-image:generateContent" in second_url


def test_gemini_imagen_404_clear_error_when_no_fallback():
    err_404_resp = MagicMock()
    err_404_resp.status_code = 404
    err_404_resp.text = '{"error": {"code": 404, "message": "models/imagen-3.0-generate-002 is not found for API version v1beta, or is not supported for predict."}}'

    fallback_err_resp = MagicMock()
    fallback_err_resp.status_code = 404
    fallback_err_resp.text = "NOT_FOUND"

    with patch("httpx.post", side_effect=[err_404_resp, fallback_err_resp]):
        with pytest.raises(ImageAIError) as exc_info:
            call_image_ai_edits(
                image_bytes=b"raw_input_image",
                prompt="Clean dust",
                config={
                    "provider": "gemini",
                    "endpoint": "",
                    "api_key": "AIzaSySecret",
                    "model": "imagen-3.0-generate-002",
                },
            )
        err = str(exc_info.value)
        assert "404" in err
        assert "gemini-2.5-flash-image" in err
