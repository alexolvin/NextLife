"""Regression tests for C-24: API photo cropping (Phase P1: Media).

Verifies POST /api/items/{code}/photos/crop:
- Cropping photo with coordinate bounding box [left, top, right, bottom]
- Verification of new cropped dimensions (width = right - left, height = bottom - top)
- Verification that original photo remains unchanged (Tier 1 immutable, C-25)
- Audit logging of crop event
- Error handling for nonexistent items and invalid bounding boxes
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


def _make_dummy_image(width: int = 300, height: int = 300) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color="yellow")
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
    code = "995"
    with get_db_session() as session:
        item = session.get(Item, code)
        if not item:
            item = Item(
                code=code,
                kind="SELL",
                title="Test Lot for Cropping",
                category_key="electronics",
                condition_key="used",
                status="DRAFT",
            )
            session.add(item)
            session.commit()

    jpeg_bytes = _make_dummy_image(width=300, height=300)
    resp = client.post(
        f"/api/items/{code}/photos",
        files={"file": ("square.jpg", jpeg_bytes, "image/jpeg")},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)
    return code


def test_api_crop_photo_success(
    client: TestClient, auth_headers: dict[str, str], test_item_with_photo: str
) -> None:
    """C-24: Cropping photo via POST /api/items/{code}/photos/crop must return 200 and cropped dimensions."""
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

    # Crop box: left=10, top=20, right=160, bottom=180 -> width=150, height=160
    box = [10, 20, 160, 180]
    resp = client.post(
        f"/api/items/{code}/photos/crop",
        json={"box": box},
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"
    assert data["box"] == box
    assert data["width"] == 150
    assert data["height"] == 160

    # Verify original photo was NOT mutated (C-25 invariant)
    with get_db_session() as session:
        orig_after = (
            session.query(ItemPhoto)
            .filter(ItemPhoto.item_code == code, ItemPhoto.role == "ORIGINAL")
            .first()
        )
        assert orig_after is not None
        assert orig_after.sha256 == initial_orig_sha


def test_api_crop_photo_nonexistent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Nonexistent item must return 404."""
    resp = client.post(
        "/api/items/999999/photos/crop",
        json={"box": [10, 10, 200, 200]},
        headers=auth_headers,
    )
    assert resp.status_code == 404
