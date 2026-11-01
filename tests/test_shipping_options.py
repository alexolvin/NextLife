"""Tests for lot delivery options constructor, auto tariffs, manual override, and calculations (T-046).

DoD:
- Constructor with auto tariff selection and manual price override tagging.
- Live calculations of buyer_total and seller_net according to policy.
- Adding and removing options updates both quantities.
- Manual price override is explicitly flagged as manual.
- Module length <= 400 lines.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from nextlife.api.main import create_app
from nextlife.core.secrets import resolve_secret
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.shipping_options import (
    calculate_carrier_tariff,
    calculate_totals,
    configure_lot_delivery_options,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import DeliveryOption


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def auth_headers():
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    return {"Authorization": f"Bearer {token}"}


def test_calculate_totals_policies():
    price = Decimal("1000.00")
    cost = Decimal("79.00")

    # 1. BUYER_PAYS
    b_tot, s_net = calculate_totals(price, cost, "BUYER_PAYS")
    assert b_tot == Decimal("1079.00")
    assert s_net == Decimal("1000.00")

    # 2. SELLER_PAYS (Free shipping for buyer)
    b_tot_free, s_net_free = calculate_totals(price, cost, "SELLER_PAYS")
    assert b_tot_free == Decimal("1000.00")
    assert s_net_free == Decimal("921.00")

    # 3. PICKUP_ONLY
    b_tot_p, s_net_p = calculate_totals(price, Decimal("0.00"), "PICKUP_ONLY")
    assert b_tot_p == Decimal("1000.00")
    assert s_net_p == Decimal("1000.00")


def test_auto_tariff_calculation_and_weight_rules():
    # Pickup always 0
    assert calculate_carrier_tariff(None, "pickup") == Decimal("0.00")

    # Zásilkovna standard <= 5kg
    assert calculate_carrier_tariff(None, "zasilkovna", weight_g=2000) == Decimal("89.00")
    # Zásilkovna heavy > 5kg
    assert calculate_carrier_tariff(None, "zasilkovna", weight_g=6000) == Decimal("149.00")

    # Balíkovna
    assert calculate_carrier_tariff(None, "balikovna", weight_g=3000) == Decimal("79.00")

    # Česká pošta standard <= 2kg, medium <= 10kg, heavy > 10kg
    assert calculate_carrier_tariff(None, "cpost", weight_g=1500) == Decimal("129.00")
    assert calculate_carrier_tariff(None, "cpost", weight_g=4000) == Decimal("169.00")
    assert calculate_carrier_tariff(None, "cpost", weight_g=12000) == Decimal("229.00")


def test_manual_price_override_flagged_in_db():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Shipping Test Item",
            category_key="general",
            price=Decimal("800.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Option 1: Auto price (89 Kč for zasilkovna)
        # Option 2: Manual override price (120 Kč for balikovna whose auto is 79 Kč)
        options = [
            {"carrier_key": "zasilkovna", "price": 89.0},
            {"carrier_key": "balikovna", "price": 120.0},
        ]

        saved = configure_lot_delivery_options(
            session=session,
            lot_code=lot_code,
            options=options,
            shipping_policy="BUYER_PAYS",
        )
        session.commit()

        assert len(saved) == 2

        # Verify database flags
        db_opts = session.scalars(
            select(DeliveryOption)
            .where(DeliveryOption.item_code == item_code)
            .order_by(DeliveryOption.price.asc())
        ).all()

        z_opt = next(o for o in db_opts if o.carrier_key == "zasilkovna")
        b_opt = next(o for o in db_opts if o.carrier_key == "balikovna")

        # Auto tariff: is_manual_price must be False
        assert z_opt.is_manual_price is False
        assert z_opt.price == Decimal("89.00")

        # Overridden tariff: is_manual_price must be True
        assert b_opt.is_manual_price is True
        assert b_opt.price == Decimal("120.00")

        # Clean up
        for o in db_opts:
            session.delete(o)
        session.commit()


def test_api_save_and_get_shipping_options(client, auth_headers):
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="API Shipping Test",
            category_key="general",
            price=Decimal("1500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]
        session.commit()

    # Save options via API
    payload = {
        "shipping_policy": "SELLER_PAYS",
        "options": [
            {"carrier_key": "pickup", "price": 0.0},
            {"carrier_key": "zasilkovna", "price": 85.0, "is_manual_price": True},
        ],
    }
    resp_post = client.post(
        f"/api/lots/{lot_code}/shipping-options",
        headers=auth_headers,
        json=payload,
    )
    assert resp_post.status_code == 200
    data = resp_post.json()
    assert data["shipping_policy"] == "SELLER_PAYS"
    assert len(data["options"]) == 2

    # Verify seller net deducted for free shipping
    zas = next(o for o in data["options"] if o["carrier_key"] == "zasilkovna")
    assert zas["is_manual_price"] is True
    assert zas["buyer_total"] == 1500.0  # Buyer pays item price
    assert zas["seller_net"] == 1415.0  # 1500 - 85

    # GET endpoint returns same
    resp_get = client.get(f"/api/lots/{lot_code}/shipping-options", headers=auth_headers)
    assert resp_get.status_code == 200
    assert resp_get.json()["lot_code"] == lot_code

    # Clean up
    with get_db_session() as session:
        session.query(DeliveryOption).filter(DeliveryOption.item_code == item_code).delete()
        session.commit()


def test_ui_shipping_constructor_html(client, auth_headers):
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="UI Shipping Test",
            category_key="general",
            price=Decimal("1200.00"),
        )
        item_code = created["item_code"]
        session.commit()

    # Конструктор доставки теперь живёт на экране предмета Склада
    # (standalone /ui/lots/{code}/shipping редиректит на /ui/stock/{code}#delivery).
    resp = client.get(f"/ui/stock/{item_code}", headers=auth_headers)
    assert resp.status_code == 200
    html = resp.text

    # Verify Alpine constructor initialization
    assert 'x-data="shippingConstructor(' in html
    assert 'buyerTotal(opt)' in html
    assert 'sellerNet(opt)' in html

    # Verify constructor container and controls (testids из _shipping_constructor.html;
    # input-item-price / select-shipping-policy были на удалённой standalone-странице).
    assert 'data-testid="shipping-constructor"' in html
    assert 'data-testid="select-carrier-to-add"' in html
    assert 'data-testid="btn-add-option"' in html
    assert 'data-testid="btn-remove-option"' in html
    assert 'data-testid="manual-price-badge"' in html
    assert 'data-testid="auto-price-badge"' in html
    assert 'data-testid="buyer-total"' in html
    assert 'data-testid="seller-net"' in html
    assert 'data-testid="btn-save-shipping"' in html
