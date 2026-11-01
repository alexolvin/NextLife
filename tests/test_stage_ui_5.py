"""Unit tests for Stage 5 (Item Card, Stepper, Price Research, Describe, Warranty)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.core.status import ItemStatus
from nextlife.services.stock import (
    check_item_ready_for_sale,
    dispose_item,
    return_item_warranty,
    save_price_estimate,
    set_item_ready_for_sale,
)
from nextlife.storage.models import Item, Lot


def test_item_card_stepper_and_panels() -> None:
    """Verify that item card has 6-stage stepper and corresponding stage panels (TZ §5.1)."""
    app = create_app()
    client = TestClient(app)
    cookies = {"nextlife_token": "test_token_secret"}

    item_dict = {
        "code": "101",
        "title": "Ноутбук Dell XPS 15",
        "status": "NEW",
        "status_badge_cls": "badge-info",
        "storage_location": "Полка A-1",
        "target_price": "25000",
        "target_currency": "CZK",
        "warranty_months": 6,
        "warranty_terms": "14 дней на проверку",
        "warranty_until": (datetime.now(UTC) + timedelta(days=60)).date().isoformat(),
        "warranty_days_left": 60,
        "price_estimate_low": "22000",
        "price_estimate_median": "25000",
        "price_estimate_high": "28000",
        "price_estimate_currency": "CZK",
        "price_estimate_at": datetime.now(UTC).isoformat(),
        "description": "Отличный ноутбук в полном комплекте",
        "description_lang": "ru",
        "usage": "Для работы и графики",
        "specs": "i7, 32GB RAM, 1TB SSD",
        "notes": "Проверен",
        "photos": [{"id": "p1", "url": "/media/p1.jpg", "broken": False, "is_cover": True}],
        "listings": [],
        "listings_count": 0,
        "deal": None,
        "weight_g": 2000,
        "packed_weight_g": 2500,
        "dim_l_mm": 350,
        "dim_w_mm": 240,
        "dim_h_mm": 30,
        "shipping_policy": "BUYER_PAYS",
    }

    with patch("nextlife.api.routes_stock.get_stock_item", return_value=item_dict), \
         patch("nextlife.api.routes_stock.logical_photo_entries", return_value=item_dict["photos"]), \
         patch("nextlife.api.routes_stock.get_lot_delivery_summary", return_value={}):
        r = client.get("/ui/stock/101", cookies=cookies)
        assert r.status_code == 200
        html = r.text

        # Stepper tabs (6 tabs)
        assert 'data-testid="item-stepper"' in html
        assert 'data-testid="tab-price"' in html
        assert 'data-testid="tab-photos"' in html
        assert 'data-testid="tab-describe"' in html
        assert 'data-testid="tab-listings"' in html
        assert 'data-testid="tab-sale"' in html
        assert 'data-testid="tab-warranty"' in html

        # Stage panels
        assert 'data-testid="stage-price-panel"' in html
        assert 'data-testid="stage-photos-panel"' in html
        assert 'data-testid="stage-describe-panel"' in html
        assert 'data-testid="stage-listings-panel"' in html
        assert 'data-testid="stage-sale-panel"' in html
        assert 'data-testid="stage-warranty-panel"' in html

        # Buttons
        assert 'data-testid="btn-back-stock"' in html
        assert 'data-testid="btn-dispose"' in html
        assert 'data-testid="btn-delete"' in html
        assert 'data-testid="btn-sell-median"' in html
        assert 'data-testid="btn-ready-for-sale"' in html


def test_ready_for_sale_checklist_validation() -> None:
    """Verify that checklist validates 5 criteria before allowing READY_FOR_SALE (§5.5.5)."""
    session = MagicMock()
    item = MagicMock(spec=Item)
    item.code = "201"
    item.status = "NEW"
    item.description = ""
    item.weight_g = None
    item.dim_l_mm = None
    item.dim_w_mm = None
    item.dim_h_mm = None
    item.target_price = None
    item.price_estimate_median = None

    lot = MagicMock(spec=Lot)
    lot.code = "201"
    lot.shipping_policy = None

    session.get.return_value = item
    session.scalar.return_value = lot

    # Mock photos count = 0
    with patch("nextlife.services.stock._get_item", return_value=item):
        session.scalar.side_effect = lambda stmt: 0 if "item_photo" in str(stmt).lower() else lot
        ready, missing = check_item_ready_for_sale(session, "201")
        assert ready is False
        assert len(missing) == 5  # all 5 criteria missing

        # Now satisfy all criteria
        item.description = "Полноценное описание предмета"
        item.weight_g = 1500
        item.dim_l_mm = 300
        item.dim_w_mm = 200
        item.dim_h_mm = 50
        item.target_price = Decimal("5000")
        lot.shipping_policy = "BUYER_PAYS"

        session.scalar.side_effect = lambda stmt: 1 if "item_photo" in str(stmt).lower() else lot
        ready_ok, missing_ok = set_item_ready_for_sale(session, "201")
        assert ready_ok is True
        assert len(missing_ok) == 0
        assert item.status == ItemStatus.READY_FOR_SALE.value


def test_price_estimate_and_dispose_transitions() -> None:
    """Verify price estimate saves and item disposition (§5.2)."""
    session = MagicMock()
    item = MagicMock(spec=Item)
    item.code = "301"
    item.status = ItemStatus.DRAFT.value
    session.get.return_value = item

    with patch("nextlife.services.stock._get_item", return_value=item):
        save_price_estimate(
            session=session,
            code="301",
            target_price="12000",
            currency="CZK",
            median="12000",
            low="10000",
            high="14000",
        )
        assert item.target_price == Decimal("12000")
        assert item.price_estimate_median == Decimal("12000")
        assert item.status == "ESTIMATED"

    # Dispose item
    with patch("nextlife.services.stock._get_item", return_value=item), \
         patch("nextlife.services.stock.unpublish_listings_now", return_value=[]):
        dispose_item(session, "301", reason="Нерентабельно")
        assert item.status == ItemStatus.DISPOSED.value


def test_warranty_return_and_expiration() -> None:
    """Verify warranty tracking and return (§5.7)."""
    session = MagicMock()
    item = MagicMock(spec=Item)
    item.code = "401"
    item.status = ItemStatus.SOLD.value
    item.warranty_months = 12
    item.warranty_until = datetime.now(UTC).date() + timedelta(days=90)

    with patch("nextlife.services.stock._get_item", return_value=item):
        return_item_warranty(session, "401", reason="Не работает разъем")
        assert item.status == ItemStatus.RETURNED.value
