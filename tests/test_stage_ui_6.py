"""Test suite for Stage 6: photo stand, photo editor and image ai (TZ §5.3, §5.4, §11.1)."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nextlife.media.ai_edit import (
    ImageAINotConfiguredError,
    ai_remove_dust,
    call_image_ai_edits,
    get_image_ai_config,
    is_image_ai_configured,
    undo_photo,
)
from nextlife.storage.models import Item, ItemPhoto


def _make_dummy_png_bytes() -> bytes:
    img = Image.new("RGB", (100, 100), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_image_ai_config_unconfigured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("nextlife.media.ai_edit.get_data_dir", lambda: tmp_path)
    monkeypatch.delenv("NEXTLIFE_IMAGE_AI_ENDPOINT", raising=False)
    monkeypatch.delenv("NEXTLIFE_IMAGE_AI_KEY", raising=False)
    assert not is_image_ai_configured()
    cfg = get_image_ai_config()
    assert cfg["endpoint"] == ""

    with pytest.raises(ImageAINotConfiguredError):
        call_image_ai_edits(b"dummy", "prompt")


def test_call_image_ai_edits_mocked(monkeypatch: pytest.MonkeyPatch):
    dummy_png = _make_dummy_png_bytes()
    b64_str = base64.b64encode(dummy_png).decode("ascii")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [{"b64_json": b64_str}]
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        res = call_image_ai_edits(
            image_bytes=dummy_png,
            prompt="clean dust",
            config={"endpoint": "https://api.openai.com/v1", "api_key": "test-key", "model": "dall-e-2"},
        )
        assert res == dummy_png
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.openai.com/v1/images/edits"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert kwargs["data"]["prompt"] == "clean dust"


def test_ai_remove_dust_and_undo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nextlife.media.ai_edit.get_media_dir", lambda: tmp_path)
    dummy_png = _make_dummy_png_bytes()
    b64_str = base64.b64encode(dummy_png).decode("ascii")

    # Create dummy parent photo on disk
    item_dir = tmp_path / "items" / "ITM-001"
    item_dir.mkdir(parents=True)
    orig_path = item_dir / "orig.jpg"
    orig_path.write_bytes(dummy_png)

    parent_photo = ItemPhoto(
        id="photo-1",
        item_code="ITM-001",
        role="ORIGINAL",
        path=str(orig_path),
        width=100,
        height=100,
        derived_from_id=None,
        ai_generated=False,
    )
    mock_item = Item(
        code="ITM-001",
        title="Test Item",
        attrs={"photo_order": ["photo-1"], "cover_photo_id": "photo-1"},
    )

    db_store = {
        ("ItemPhoto", "photo-1"): parent_photo,
        ("Item", "ITM-001"): mock_item,
    }

    class MockSession:
        def __init__(self):
            self.added = []
            self.deleted = []

        def get(self, model, pk):
            return db_store.get((model.__name__, pk))

        def add(self, obj):
            self.added.append(obj)
            db_store[(type(obj).__name__, obj.id)] = obj

        def delete(self, obj):
            self.deleted.append(obj)
            db_store.pop((type(obj).__name__, obj.id), None)

        def flush(self):
            pass

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"b64_json": b64_str}]}

    session = MockSession()
    with patch("httpx.post", return_value=mock_resp), patch("nextlife.media.ai_edit.get_image_ai_config", return_value={"endpoint": "http://mock-ai", "api_key": "k", "model": "m"}):
        derived = ai_remove_dust(photo_id="photo-1", session=session)
        assert derived.ai_generated is True
        assert derived.derived_from_id == "photo-1"
        assert mock_item.attrs["photo_order"] == [derived.id]
        assert mock_item.attrs["cover_photo_id"] == derived.id

        # Now test undo
        restored = undo_photo(session=session, photo_id=derived.id)
        assert restored is not None
        assert restored.id == "photo-1"
        assert mock_item.attrs["photo_order"] == ["photo-1"]
        assert mock_item.attrs["cover_photo_id"] == "photo-1"


def test_photo_stand_route():
    from nextlife.api.main import create_app
    from nextlife.api.routes import check_auth

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)
    with patch("nextlife.api.routes_stock.get_db_session") as mock_session_ctx:
        session = MagicMock()
        mock_item = MagicMock()
        mock_item.code = "TST-99"
        mock_item.title = "Телефон Samsung"
        mock_item.storage_location = "A-12"
        mock_item.attrs = {"wizard_selected": []}
        session.get.return_value = mock_item
        mock_session_ctx.return_value.__enter__.return_value = session

        with patch("nextlife.api.routes_stock.logical_photo_entries", return_value=[]):
            resp = client.get("/ui/photo-stand?codes=TST-99")
            assert resp.status_code == 200
            assert "Фотостенд" in resp.text
            assert "TST-99" in resp.text
            assert "Телефон Samsung" in resp.text
            assert 'data-testid="photo-stand-screen"' in resp.text
            assert 'data-testid="btn-capture"' in resp.text


def test_no_forbidden_words_in_stage_6_templates():
    forbidden = ["лот", "мастер создания лота", "мастер лота", "dry-run", "фрейм"]
    files = [
        Path("templates/web/photo_stand.html"),
        Path("templates/web/_photo_gallery.html"),
    ]
    for f in files:
        if f.exists():
            content = f.read_text(encoding="utf-8").lower()
            for word in forbidden:
                assert word not in content, f"Forbidden word '{word}' found in {f}"
