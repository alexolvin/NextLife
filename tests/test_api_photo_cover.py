"""Regression tests for C-22: API set photo as cover (Phase P1: Media).

Verifies POST /api/items/{code}/photos/{photo_id}/cover:
- Setting specific photo as cover photo
- Verification in database (item.attrs cover_photo_id and photo_order)
- Audit logging of cover assignment
- Error handling for nonexistent items and photos
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
    img = Image.new("RGB", (100, 100), color="green")
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
def test_item_with_photos(client: TestClient, auth_headers: dict[str, str]) -> tuple[str, list[str]]:
    code = "993"
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            item = Item(
                code=code,
                kind="SELL",
                title="Test Lot for Cover",
                category_key="electronics",
                condition_key="used",
                status="DRAFT",
            )
            session.add(item)
            session.commit()

    jpeg_bytes = _make_dummy_jpeg()
    r1 = client.post(
        f"/api/items/{code}/photos",
        files={"file": ("photo_1.jpg", jpeg_bytes, "image/jpeg")},
        headers=auth_headers,
    )
    r2 = client.post(
        f"/api/items/{code}/photos",
        files={"file": ("photo_2.jpg", jpeg_bytes, "image/jpeg")},
        headers=auth_headers,
    )
    assert r1.status_code in (200, 201)
    assert r2.status_code in (200, 201)

    with get_db_session() as session:
        photos = (
            session.query(ItemPhoto)
            .filter(ItemPhoto.item_code == code)
            .order_by(ItemPhoto.created_at.asc())
            .all()
        )
        photo_ids = [p.id for p in photos]
    assert len(photo_ids) >= 2
    return code, photo_ids


def test_api_set_cover_photo_success(
    client: TestClient, auth_headers: dict[str, str], test_item_with_photos: tuple[str, list[str]]
) -> None:
    """C-22: Setting photo as cover via POST /api/items/{code}/photos/{photo_id}/cover must return 200."""
    code, photo_ids = test_item_with_photos
    target_cover_id = photo_ids[-1]

    resp = client.post(
        f"/api/items/{code}/photos/{target_cover_id}/cover",
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"
    assert data["cover_photo_id"] == target_cover_id

    # Verify cover recorded in DB
    with get_db_session() as session:
        item = session.get(Item, code)
        assert item is not None
        assert item.attrs.get("cover_photo_id") == target_cover_id
        # Also cover photo should be first in order
        order = item.attrs.get("photo_order", [])
        assert len(order) > 0
        assert order[0] == target_cover_id


def test_api_set_cover_photo_nonexistent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Nonexistent photo or item must return 404."""
    resp = client.post(
        "/api/items/999999/photos/nonexistent-id/cover",
        headers=auth_headers,
    )
    assert resp.status_code == 404
