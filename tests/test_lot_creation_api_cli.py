from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.cli.main import handle_cli
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Lot


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers():
    from nextlife.core.secrets import resolve_secret
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    return {"Authorization": f"Bearer {token}"}


def test_service_create_item_and_lot():
    with get_db_session() as session:
        res = create_item_and_lot(
            session=session,
            title="Dell UltraSharp U2720Q 27\"",
            category_key="electronics",
            price=Decimal("8500"),
            currency="CZK",
            shipping_policy="BUYER_PAYS",
        )
        session.commit()

        assert res["status"] == "created"
        assert res["item_code"]
        assert res["lot_code"] == res["item_code"]
        assert res["price"] == "8500"

        # Verify DB records
        item = session.get(Item, res["item_code"])
        assert item is not None
        assert item.title == "Dell UltraSharp U2720Q 27\""

        lot = session.query(Lot).filter(Lot.code == res["lot_code"]).first()
        assert lot is not None
        assert lot.price_gross == Decimal("8500")
        assert lot.item_id == item.code


def test_api_create_lot(client, auth_headers):
    payload = {
        "title": "Logitech MX Master 3S",
        "category": "electronics",
        "price": 1800.0,
        "currency": "CZK",
        "condition": "used",
    }
    response = client.post("/api/lots", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "created"
    assert data["title"] == "Logitech MX Master 3S"
    assert data["price"] == "1800.0"

    # Also test /api/items alias
    resp2 = client.post("/api/items", json=payload, headers=auth_headers)
    assert resp2.status_code == 201


def test_cli_item_create_shares_service(
    live_server_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """The CLI item create must hit the live API and share the create service."""
    import json

    monkeypatch.setenv("NEXTLIFE_BASE_URL", live_server_url)
    ret = handle_cli([
        "item", "create",
        "--title", "Keychron K2 Wireless Keyboard",
        "--category", "electronics",
        "--price", "1900",
    ])
    assert ret == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "DRAFT"
    assert data["title"] == "Keychron K2 Wireless Keyboard"
    # ТЗ «Склад» §5.2.1: POST /api/oc/items returns the full stock card.
    assert Decimal(data["target_price"]) == Decimal("1900")
