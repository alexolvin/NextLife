"""ТЗ «Склад» этап 5 — шлюз /api/oc/stock + CLI (§5.4).

Covers: every §5.1 route (success / 401 / 404 / 422), the delete sequence
409 → 409 → 200, the CLI exit-code mapping (409 → 4, 404/422 → 2), and
§5.2.1 (POST /api/oc/items creates the same record as POST /api/oc/stock).
"""

import os
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import clean_database

from nextlife.api.main import create_app
from nextlife.storage.db import get_db_session
from nextlife.storage.models import ItemPhoto, Listing


class _StubConnector:
    """Заглушка коннектора для тестов снятия (no live marketplace traffic)."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.calls: list[str] = []

    def unpublish(self, external_id: str) -> None:
        self.calls.append(external_id)
        if self.exc is not None:
            raise self.exc


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def oc_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['NEXTLIFE_OPENCLAW_TOKEN']}"}


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    with get_db_session() as session:
        clean_database(session)
    yield
    with get_db_session() as session:
        clean_database(session)


def _add_listing(session: Any, code: str, mp_key: str = "bazos_cz", status: str = "PUBLISHED", external_id: str = "ext-1") -> None:
    session.add(
        Listing(
            item_code=code,
            marketplace_key=mp_key,
            price=Decimal("1000"),
            currency="CZK",
            price_base=Decimal("1000"),
            status=status,
            external_id=external_id,
        )
    )


def _create_stock(client: TestClient, oc_headers: dict[str, str], **extra: Any) -> str:
    body = {"title": "OC Stock Item", **extra}
    resp = client.post("/api/oc/stock", json=body, headers=oc_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["code"]


# --- Auth (401 without token) -----------------------------------------------


def test_stock_list_401_without_token(client: TestClient) -> None:
    assert client.get("/api/oc/stock").status_code == 401


def test_stock_show_401_without_token(client: TestClient) -> None:
    assert client.get("/api/oc/stock/ABC").status_code == 401


# --- List --------------------------------------------------------------------


def test_stock_list_empty(client: TestClient, oc_headers: dict[str, str]) -> None:
    resp = client.get("/api/oc/stock", headers=oc_headers)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "count": 0}


def test_stock_list_returns_items(client: TestClient, oc_headers: dict[str, str]) -> None:
    _create_stock(client, oc_headers)
    data = client.get("/api/oc/stock", headers=oc_headers).json()
    assert data["count"] == 1
    assert data["items"][0]["title"] == "OC Stock Item"


def test_stock_list_invalid_group_422(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/stock?group=bogus", headers=oc_headers).status_code == 422


# --- Create ------------------------------------------------------------------


def test_stock_create_success(client: TestClient, oc_headers: dict[str, str]) -> None:
    resp = client.post(
        "/api/oc/stock",
        json={"title": "New", "target_price": 100, "target_currency": "CZK", "storage_location": "Стеллаж 1"},
        headers=oc_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["title"] == "New"
    assert Decimal(data["target_price"]) == Decimal("100")
    assert data["storage_location"] == "Стеллаж 1"


def test_stock_create_missing_title_422(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.post("/api/oc/stock", json={}, headers=oc_headers).status_code == 422


# --- Show --------------------------------------------------------------------


def test_stock_show_success(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_stock(client, oc_headers)
    resp = client.get(f"/api/oc/stock/{code}", headers=oc_headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == code


def test_stock_show_404(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/stock/ZZZ", headers=oc_headers).status_code == 404


# --- Update (PATCH) ----------------------------------------------------------


def test_stock_patch_success(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_stock(client, oc_headers)
    resp = client.patch(
        f"/api/oc/stock/{code}", json={"title": "Updated", "storage_location": "Стеллаж 2"}, headers=oc_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Updated"
    assert resp.json()["storage_location"] == "Стеллаж 2"


def test_stock_patch_404(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.patch("/api/oc/stock/ZZZ", json={"title": "X"}, headers=oc_headers).status_code == 404


# --- Sold --------------------------------------------------------------------


def test_stock_sold_success(client: TestClient, oc_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from nextlife.connectors import registry

    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: _StubConnector())
    code = _create_stock(client, oc_headers)
    resp = client.post(f"/api/oc/stock/{code}/sold", headers=oc_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SOLD"


def test_stock_sold_404(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.post("/api/oc/stock/ZZZ/sold", headers=oc_headers).status_code == 404


# --- Delete preview ----------------------------------------------------------


def test_stock_delete_preview(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_stock(client, oc_headers)
    resp = client.get(f"/api/oc/stock/{code}/delete-preview", headers=oc_headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == code


def test_stock_delete_preview_404(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/stock/ZZZ/delete-preview", headers=oc_headers).status_code == 404


# --- Delete sequence 409 → 409 → 200 -----------------------------------------


def test_stock_delete_sequence(client: TestClient, oc_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from nextlife.connectors import registry

    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: _StubConnector())
    code = _create_stock(client, oc_headers)
    with get_db_session() as session:
        _add_listing(session, code)
        session.commit()

    # Rule 1: no confirm → 409 with delete_preview.
    r1 = client.delete(f"/api/oc/stock/{code}", headers=oc_headers)
    assert r1.status_code == 409
    assert "delete_preview" in r1.json()

    # Rule 2: confirm but no confirm_listings, with listings → 409 + listing list.
    r2 = client.delete(f"/api/oc/stock/{code}?confirm=true", headers=oc_headers)
    assert r2.status_code == 409
    assert "listings" in r2.json()

    # Rule 3: confirm + confirm_listings → 200.
    r3 = client.delete(f"/api/oc/stock/{code}?confirm=true&confirm_listings=true", headers=oc_headers)
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "DELETED"


def test_stock_delete_404(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.delete("/api/oc/stock/ZZZ?confirm=true", headers=oc_headers).status_code == 404


# --- Photos ------------------------------------------------------------------


def test_stock_photos_empty(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_stock(client, oc_headers)
    resp = client.get(f"/api/oc/stock/{code}/photos", headers=oc_headers)
    assert resp.status_code == 200
    assert resp.json()["photos"] == []


def test_stock_photos_404(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/stock/ZZZ/photos", headers=oc_headers).status_code == 404


def test_stock_photo_delete_success(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_stock(client, oc_headers)
    with get_db_session() as session:
        photo = ItemPhoto(
            item_code=code, role="ORIGINAL", path="/tmp/oc_stock_test.jpg",
            sha256="a" * 64, width=10, height=10, transform_log={}, ai_generated=False, vlm_verdict={},
        )
        session.add(photo)
        session.commit()
        photo_id = photo.id
    resp = client.delete(f"/api/oc/stock/{code}/photos/{photo_id}", headers=oc_headers)
    assert resp.status_code == 200, resp.text
    assert photo_id in resp.json()["deleted_ids"]


def test_stock_photo_delete_404(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.delete("/api/oc/stock/ZZZ/photos/p1", headers=oc_headers).status_code == 404


# --- §5.2.1: POST /api/oc/items == POST /api/oc/stock ------------------------


def test_oc_items_and_stock_create_same_record(client: TestClient, oc_headers: dict[str, str]) -> None:
    r1 = client.post(
        "/api/oc/items",
        json={"title": "Same", "category": "electronics", "price": 100, "currency": "CZK"},
        headers=oc_headers,
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/api/oc/stock", json={"title": "Same2", "target_price": 100, "target_currency": "CZK"}, headers=oc_headers
    )
    assert r2.status_code == 201, r2.text
    # Both return the full stock card with the same shape.
    for r in (r1, r2):
        data = r.json()
        assert data["status"] == "DRAFT"
        assert "target_price" in data
        assert "storage_location" in data


# --- CLI exit codes (§5.3: 409 → 4, 404/422 → 2) -----------------------------


def test_cli_exit_code_409_is_4() -> None:
    from nextlife.cli.main import _api_error_exit_code

    assert _api_error_exit_code(409) == 4


def test_cli_exit_code_404_is_2() -> None:
    from nextlife.cli.main import _api_error_exit_code

    assert _api_error_exit_code(404) == 2


def test_cli_exit_code_422_is_2() -> None:
    from nextlife.cli.main import _api_error_exit_code

    assert _api_error_exit_code(422) == 2


def test_parse_set_flags() -> None:
    from nextlife.cli.commands import _parse_set_flags

    assert _parse_set_flags({"set": "title=X"}) == {"title": "X"}
    assert _parse_set_flags({"set": ["title=X", "target_price=100"]}) == {"title": "X", "target_price": "100"}
    assert _parse_set_flags({}) == {}
