"""Regression tests for C-16: API photo upload without browser (Phase P1: Media).

Verifies uploading photos via /api/items/{code}/photos with both:
- singular 'file' field name
- batch 'files' field name
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import io
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.core.secrets import resolve_secret
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto


def _make_dummy_jpeg() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_item() -> str:
    code = "991"
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            item = Item(
                code=code,
                kind="SELL",
                title="Test GPU for Photo Upload",
                category_key="electronics",
                condition_key="used",
                status="DRAFT",
            )
            session.add(item)
            session.commit()
    return code


def test_api_upload_photo_with_single_file_field(
    client: TestClient, auth_headers: dict[str, str], test_item: str
) -> None:
    """C-16: Upload single photo via API using 'file' field name must return 200/201, not 422."""
    jpeg_bytes = _make_dummy_jpeg()
    files = {"file": ("api_test.jpg", jpeg_bytes, "image/jpeg")}
    resp = client.post(
        f"/api/items/{test_item}/photos",
        files=files,
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), f"Expected 200/201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"
    assert data["uploaded_count"] >= 1

    # Verify in DB
    with get_db_session() as session:
        photo = (
            session.query(ItemPhoto)
            .filter(ItemPhoto.item_code == test_item)
            .order_by(ItemPhoto.created_at.desc())
            .first()
        )
        assert photo is not None


def test_api_upload_photos_with_batch_files_field(
    client: TestClient, auth_headers: dict[str, str], test_item: str
) -> None:
    """Upload multiple photos via API using 'files' field name must return 200/201."""
    jpeg_bytes = _make_dummy_jpeg()
    files = [
        ("files", ("batch_1.jpg", jpeg_bytes, "image/jpeg")),
        ("files", ("batch_2.jpg", jpeg_bytes, "image/jpeg")),
    ]
    resp = client.post(
        f"/api/items/{test_item}/photos",
        files=files,
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), f"Expected 200/201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"
    assert data["uploaded_count"] >= 2
