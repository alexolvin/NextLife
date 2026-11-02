"""Tests for Cloudflare Workers AI integration, per-operation models, and background replacement.

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nextlife.api.main import create_app
from nextlife.media.ai_edit import (
    ImageAIError,
    ai_remove_dust,
    ai_replace_bg,
    ai_selling_image,
    call_image_ai_edits,
    get_image_ai_config,
    load_settings,
)
from nextlife.media.ai_providers import call_cloudflare_image


def _make_dummy_jpeg() -> bytes:
    img = Image.new("RGB", (64, 64), color=(30, 60, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_cloudflare_workers_ai_direct_binary():
    dummy_jpeg = _make_dummy_jpeg()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    mock_resp.content = dummy_jpeg

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        res = call_cloudflare_image(
            prompt="Isolate product on white background",
            image_bytes=b"raw_bytes",
            cfg={
                "endpoint": "https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/",
                "api_key": "cf_token_abc",
                "model": "@cf/runwayml/stable-diffusion-v1-5-inpainting",
            },
        )
        assert res == dummy_jpeg
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/@cf/runwayml/stable-diffusion-v1-5-inpainting"
        assert kwargs["headers"]["Authorization"] == "Bearer cf_token_abc"
        assert "image_b64" in kwargs["json"]
        assert kwargs["json"]["prompt"] == "Isolate product on white background"


def test_cloudflare_workers_ai_json_b64():
    dummy_jpeg = _make_dummy_jpeg()
    b64_str = base64.b64encode(dummy_jpeg).decode("ascii")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"result": {"image": "..."}}'
    mock_resp.json.return_value = {"result": {"image": b64_str}}

    with patch("httpx.post", return_value=mock_resp):
        res = call_cloudflare_image(
            prompt="Remove dust",
            image_bytes=b"raw_bytes",
            cfg={
                "endpoint": "https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/",
                "api_key": "cf_token_abc",
            },
            model="@cf/runwayml/stable-diffusion-v1-5-inpainting",
        )
        assert res == dummy_jpeg


def test_per_operation_model_selection():
    dummy_jpeg = _make_dummy_jpeg()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    mock_resp.content = dummy_jpeg

    cfg = {
        "provider": "cloudflare",
        "endpoint": "https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/",
        "api_key": "token123",
        "model": "fallback_model",
        "model_dust": "@cf/runwayml/stable-diffusion-v1-5-inpainting",
        "model_selling": "@cf/black-forest-labs/flux-1-schnell",
        "model_bg": "@cf/briaai/rmbg-1.4",
    }

    # 1. Calling with model_override for Dust
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        call_image_ai_edits(b"img", "dust", config=cfg, model_override=cfg["model_dust"])
        assert "@cf/runwayml/stable-diffusion-v1-5-inpainting" in mock_post.call_args[0][0]

    # 2. Calling with model_override for Selling
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        call_image_ai_edits(b"img", "selling", config=cfg, model_override=cfg["model_selling"])
        assert "@cf/black-forest-labs/flux-1-schnell" in mock_post.call_args[0][0]

    # 3. Calling with model_override for Background Replacement
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        call_image_ai_edits(b"img", "bg replace", config=cfg, model_override=cfg["model_bg"])
        assert "@cf/briaai/rmbg-1.4" in mock_post.call_args[0][0]


def test_settings_save_and_load_cloudflare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nextlife.services.llm_filter.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("nextlife.media.ai_edit.get_data_dir", lambda: tmp_path)

    from nextlife.api.routes import check_auth

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    # Save Cloudflare settings with 3 separate models
    resp = client.post(
        "/ui/settings/ai",
        data={
            "llm_provider": "gemini",
            "llm_endpoint": "",
            "llm_api_key": "AIzaSySecret",
            "llm_model": "gemini-1.5-flash",
            "image_ai_provider": "cloudflare",
            "image_ai_endpoint": "https://api.cloudflare.com/client/v4/accounts/acc999/ai/run/",
            "image_ai_api_key": "cf_secret_token",
            "image_ai_model": "",
            "image_ai_model_dust": "@cf/runwayml/stable-diffusion-v1-5-inpainting",
            "image_ai_model_selling": "@cf/black-forest-labs/flux-1-schnell",
            "image_ai_model_bg": "@cf/runwayml/stable-diffusion-v1-5-inpainting",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    cfg = get_image_ai_config()
    assert cfg["provider"] == "cloudflare"
    assert cfg["endpoint"] == "https://api.cloudflare.com/client/v4/accounts/acc999/ai/run/"
    assert cfg["api_key"] == "cf_secret_token"
    assert cfg["model_dust"] == "@cf/runwayml/stable-diffusion-v1-5-inpainting"
    assert cfg["model_selling"] == "@cf/black-forest-labs/flux-1-schnell"
    assert cfg["model_bg"] == "@cf/runwayml/stable-diffusion-v1-5-inpainting"


def test_cloudflare_account_id_placeholder_detected():
    with pytest.raises(ImageAIError) as exc_info:
        call_cloudflare_image(
            prompt="Remove dust",
            image_bytes=b"raw_bytes",
            cfg={
                "endpoint": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/",
                "api_key": "cf_token_abc",
            },
        )
    assert "{account_id}" in str(exc_info.value)
    assert "Cloudflare Account ID" in str(exc_info.value)


def test_cloudflare_short_account_id_expansion():
    dummy_jpeg = _make_dummy_jpeg()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    mock_resp.content = dummy_jpeg

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        res = call_cloudflare_image(
            prompt="Selling photo",
            image_bytes=dummy_jpeg,
            cfg={
                "endpoint": "a1b2c3d4e5f6789012345678abcdef01",
                "api_key": "cf_token_abc",
                "model": "@cf/black-forest-labs/flux-1-schnell",
            },
        )
        assert res == dummy_jpeg
        call_url = mock_post.call_args[0][0]
        assert call_url == "https://api.cloudflare.com/client/v4/accounts/a1b2c3d4e5f6789012345678abcdef01/ai/run/@cf/black-forest-labs/flux-1-schnell"


def test_ai_remove_dust_save_derived_photo_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nextlife.media.ai_edit.get_media_dir", lambda: tmp_path)
    from nextlife.storage.models import Item, ItemPhoto

    dummy_jpeg = _make_dummy_jpeg()
    input_path = tmp_path / "orig.jpg"
    input_path.write_bytes(dummy_jpeg)

    item = Item(code="777", title="Test Item", attrs={"cover_photo_id": "p1", "photo_order": ["p1"]})
    photo = ItemPhoto(
        id="p1",
        item_code="777",
        role="ORIGINAL",
        path=str(input_path),
        sha256="abc",
        width=64,
        height=64,
        transform_log=[],
        vlm_verdict={},
    )
    mock_session = MagicMock()

    def mock_get(entity, pk):
        if entity == ItemPhoto and pk == "p1":
            return photo
        if entity == Item and pk == "777":
            return item
        return None

    mock_session.get.side_effect = mock_get

    with patch("nextlife.media.ai_edit.call_image_ai_edits", return_value=dummy_jpeg):
        new_photo = ai_remove_dust(photo_id="p1", session=mock_session)

    assert new_photo.ai_generated is True
    assert new_photo.derived_from_id == "p1"
    assert item.attrs["cover_photo_id"] == new_photo.id
    assert new_photo.id in item.attrs["photo_order"]


def test_cloudflare_flux_omits_image_b64():
    dummy_jpeg = _make_dummy_jpeg()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/jpeg"}
    mock_resp.content = dummy_jpeg

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        res = call_cloudflare_image(
            prompt="A sleek studio rendering",
            image_bytes=dummy_jpeg,
            cfg={"endpoint": "https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/"},
            model="@cf/black-forest-labs/flux-1-schnell",
        )
        assert res == dummy_jpeg
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        # Flux rejects requests containing image_b64
        assert "image_b64" not in kwargs["json"]
        assert kwargs["json"]["prompt"] == "A sleek studio rendering"


def test_cloudflare_bria_remove_background():
    dummy_jpeg = _make_dummy_jpeg()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"state": "Completed", "result": {"image": "https://cdn.example.com/cutout.png"}}'
    mock_resp.json.return_value = {"result": {"image": "https://cdn.example.com/cutout.png"}}

    mock_img_resp = MagicMock()
    mock_img_resp.status_code = 200
    mock_img_resp.content = dummy_jpeg

    with patch("httpx.post", return_value=mock_resp) as mock_post, patch("httpx.get", return_value=mock_img_resp) as mock_get:
        res = call_cloudflare_image(
            prompt="Isolate object",
            image_bytes=dummy_jpeg,
            cfg={"endpoint": "https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/"},
            model="@cf/bria/remove-background",
        )
        assert res == dummy_jpeg
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert "image" in kwargs["json"]
        assert kwargs["json"]["image"].startswith("data:image/jpeg;base64,")
        assert "prompt" not in kwargs["json"]
        mock_get.assert_called_once_with("https://cdn.example.com/cutout.png", timeout=30.0)



