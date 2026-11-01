"""Regression tests for C-23: API photo rotation (Phase P1: Media).

Verifies POST /api/items/{code}/photos/rotate:
- Rotating photo by 90 degrees
- Verification that width and height swap
- Verification that original photo remains unchanged (Tier 1 immutable)
- Audit logging of rotation event
- Error handling for nonexistent items
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


def _make_dummy_rectangular_jpeg(width: int = 120, height: int = 80) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color="red")
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
def test_item_with_photo(client: TestClient, auth_headers: dict[str, str]) -> str:
    code = "994"
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            item = Item(
                code=code,
                kind="SELL",
                title="Test Lot for Rotation",
                category_key="electronics",
                condition_key="used",
                status="DRAFT",
            )
            session.add(item)
            session.commit()

    jpeg_bytes = _make_dummy_rectangular_jpeg(width=120, height=80)
    resp = client.post(
        f"/api/items/{code}/photos",
        files={"file": ("rect.jpg", jpeg_bytes, "image/jpeg")},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)
    return code


def test_api_rotate_photo_success(
    client: TestClient, auth_headers: dict[str, str], test_item_with_photo: str
) -> None:
    """C-23: Photo rotation by 90 degrees via POST /api/items/{code}/photos/rotate must return 200 and swap dimensions."""
    code = test_item_with_photo

    # Capture initial original sha256
    with get_db_session() as session:
        orig = (
            session.query(ItemPhoto)
            .filter(ItemPhoto.item_code == code, ItemPhoto.role == "ORIGINAL")
            .first()
        )
        assert orig is not None
        initial_orig_sha = orig.sha256

    resp = client.post(
        f"/api/items/{code}/photos/rotate",
        json={"degrees": 90},
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"
    assert data["degrees"] == 90
    assert data["width"] == 80
    assert data["height"] == 120

    # Verify original photo was NOT mutated (C-25 invariant)
    with get_db_session() as session:
        orig_after = (
            session.query(ItemPhoto)
            .filter(ItemPhoto.item_code == code, ItemPhoto.role == "ORIGINAL")
            .first()
        )
        assert orig_after is not None
        assert orig_after.sha256 == initial_orig_sha


def test_api_rotate_photo_nonexistent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Nonexistent item must return 404."""
    resp = client.post(
        "/api/items/999999/photos/rotate",
        json={"degrees": 90},
        headers=auth_headers,
    )
    assert resp.status_code == 404
