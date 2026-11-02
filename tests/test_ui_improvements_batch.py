"""Tests for user improvements:
1. Unified dispose and delete modals.
2. Marketplaces capabilities matrix shows search enabled for TrhKnih, Hyperinzerce, Annonce.
3. Photo rotation preserves #photos tab.
4. Two rotate buttons (left -90 and right +90).
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request

from nextlife.api.routes import templates
from nextlife.api.routes_photos_ops import _redirect_card, ui_rotate_item_photo
from nextlife.api.routes_settings import CAPABILITIES_MATRIX, screen_settings


def test_stock_item_unified_modals():
    """Ensure dispose and delete modals are unified and present in stock_item.html."""
    item = {
        "code": "130",
        "title": "Test GPU",
        "status": "IN_STOCK",
        "status_badge_cls": "badge-ready",
        "description": "Desc",
        "created_at": None,
        "target_price": Decimal("21000"),
        "target_currency": "CZK",
        "price_estimate_median": Decimal("21500"),
        "price_estimate_low": Decimal("19000"),
        "price_estimate_high": Decimal("24000"),
        "price_estimate_currency": "CZK",
        "photos": [],
        "weight_g": 1800,
        "packed_weight_g": 2000,
        "dim_l_mm": 350,
        "dim_w_mm": 150,
        "dim_h_mm": 70,
        "shipping_policy": "STANDARD",
        "location_rack": "A",
        "location_shelf": "1",
        "location_box": "B",
        "custom_label": "BOX-1",
        "serial_number": "SN123",
        "category": "electronics",
        "condition": "USED_GOOD",
        "defect_description": None,
    }

    req = Request({"type": "http", "method": "GET", "path": "/ui/stock/130", "headers": []})
    resp = templates.TemplateResponse(
        request=req,
        name="stock_item.html",
        context={
            "item": item,
            "it": item,
            "research": None,
            "listings": [],
            "deal": None,
            "notice": "photo_rotated",
            "photos": [],
        },
    )
    body = resp.body.decode("utf-8")

    # Check dispose modal
    assert 'id="dispose-modal"' in body
    assert 'Утилизировать предмет #130?' in body
    assert 'onclick="document.getElementById(\'dispose-modal\').classList.remove(\'hidden\')"' in body

    # Check delete modal
    assert 'id="delete-modal"' in body
    assert 'Удалить предмет #130?' in body
    assert 'onclick="document.getElementById(\'delete-modal\').classList.remove(\'hidden\')"' in body

    # Check photo rotated toast
    assert "Фотография повёрнута" in body


def test_settings_capabilities_matrix_includes_all_search():
    """TrhKnih, Hyperinzerce, and Annonce must have search: True in capabilities matrix."""
    assert CAPABILITIES_MATRIX["trhknih_cz"]["search"] is True
    assert CAPABILITIES_MATRIX["hyperinzerce_cz"]["search"] is True
    assert CAPABILITIES_MATRIX["annonce_cz"]["search"] is True

    # Render settings marketplaces tab
    req = Request({"type": "http", "method": "GET", "path": "/ui/settings?tab=marketplaces", "headers": []})
    resp = screen_settings(request=req, tab="marketplaces", auth=True)
    body = resp.body.decode("utf-8")

    assert "TrhKnih.cz" in body
    assert "Hyperinzerce.cz" in body
    assert "Annonce.cz" in body


def test_redirect_card_preserves_photos_tab():
    """_redirect_card must redirect to /ui/stock/{code}?notice=...#photos."""
    resp = _redirect_card("130", notice="photo_rotated")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/stock/130?notice=photo_rotated#photos"


@pytest.mark.anyio
async def test_ui_rotate_supports_left_and_right():
    """ui_rotate_item_photo must support degrees parameter and call rotate_photo."""
    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/ui/items/130/photos/p1/rotate",
        "headers": [],
        "query_string": b"degrees=-90",
    })

    with patch("nextlife.api.routes_photos_ops.get_db_session") as mock_db, \
         patch("nextlife.api.routes_photos_ops.rotate_photo") as mock_rotate, \
         patch("nextlife.api.routes_photos_ops.record_audit_event"):
        session = MagicMock()
        mock_db.return_value.__enter__.return_value = session
        session.get.return_value = MagicMock()
        mock_photo = MagicMock()
        mock_photo.id = "p1"
        mock_rotate.return_value = mock_photo

        resp = await ui_rotate_item_photo(
            request=req,
            code="130",
            photo_id="p1",
            degrees=-90,
            auth=True,
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/stock/130?notice=photo_rotated#photos"
        mock_rotate.assert_called_once_with(
            session=session,
            item_code="130",
            photo_id="p1",
            degrees=-90,
        )


def test_photo_gallery_renders_both_rotate_buttons():
    """_photo_gallery.html must render both left and right rotate buttons."""
    item = {
        "code": "130",
        "title": "Test GPU",
        "status": "IN_STOCK",
        "status_badge_cls": "badge-ready",
        "description": "Desc",
        "created_at": None,
        "target_price": None,
        "target_currency": "CZK",
        "price_estimate_median": None,
        "price_estimate_low": None,
        "price_estimate_high": None,
        "price_estimate_currency": "CZK",
        "photos": [
            {
                "id": "p1",
                "display_id": "p1",
                "url": "/static/test.jpg",
                "label": "Фото 1",
                "role": "ORIGINAL",
                "broken": False,
                "hints": [],
            }
        ],
        "weight_g": None,
        "packed_weight_g": None,
        "dim_l_mm": None,
        "dim_w_mm": None,
        "dim_h_mm": None,
        "shipping_policy": "STANDARD",
        "location_rack": None,
        "location_shelf": None,
        "location_box": None,
        "custom_label": None,
        "serial_number": None,
        "category": "electronics",
        "condition": "USED_GOOD",
        "defect_description": None,
    }

    req = Request({"type": "http", "method": "GET", "path": "/ui/stock/130", "headers": []})
    resp = templates.TemplateResponse(
        request=req,
        name="stock_item.html",
        context={
            "item": item,
            "it": item,
            "research": None,
            "listings": [],
            "deal": None,
            "notice": None,
            "photos": item["photos"],
        },
    )
    body = resp.body.decode("utf-8")

    # In photo ops row:
    assert 'data-testid="btn-op-rotate-left"' in body
    assert 'data-testid="btn-op-rotate"' in body
    assert "Влево 90°" in body
    assert "Вправо 90°" in body

    # In photo editor toolbar:
    assert 'data-testid="btn-editor-rotate-left"' in body
    assert 'data-testid="btn-editor-rotate"' in body


def test_item_status_transitions_to_deleted():
    from nextlife.core.status import ItemStatus, can_transition

    for st in (ItemStatus.NEW, ItemStatus.ESTIMATED, ItemStatus.PHOTOGRAPHED, ItemStatus.DESCRIBED, ItemStatus.IN_WARRANTY):
        assert can_transition(st, ItemStatus.DELETED) is True


def test_delete_stock_item_cancels_active_deals_when_confirmed():
    from nextlife.services.stock import delete_stock_item
    from nextlife.storage.models import Deal, Item, Lot

    session = MagicMock()
    item = Item(code="106", kind="SELL", title="Test Item", category_key="c", condition_key="used", status="DRAFT")
    lot = Lot(code="106", item_id="106", status="DRAFT")
    deal = Deal(id="d1", status="ACTIVE", closed_at=None, agreed_price=Decimal("100"), currency="CZK", price_base=Decimal("100"), delivery_method="x", payment_method="y")

    session.get.return_value = item
    session.scalar.return_value = lot

    with patch("nextlife.services.stock._get_item", return_value=item), \
         patch("nextlife.services.stock.can_delete_lot", return_value=(False, "Active deal")), \
         patch("nextlife.services.stock._active_listings", return_value=[]), \
         patch("nextlife.services.stock.unpublish_listings_now", return_value=[]), \
         patch("nextlife.services.stock.delete_photo_and_derived", return_value=[]), \
         patch("nextlife.services.stock.record_audit_event"):

        session.scalars.side_effect = [
            MagicMock(all=lambda: []),  # listings
            MagicMock(all=lambda: [deal]),  # active_deals
            MagicMock(all=lambda: []),  # photos
        ]

        res = delete_stock_item(session, "106", confirm_listings=True, confirm_deals=True)
        assert res["status"] == "DELETED"
        assert deal.status == "CANCELLED"
        assert deal.closed_at is not None
        assert item.status == "DELETED"
        assert lot.status == "DELETED"


def test_ui_cancel_deal_endpoint():
    from nextlife.api.routes_deal_screen import ui_cancel_deal
    from nextlife.storage.models import Deal, Lot

    session = MagicMock()
    deal = Deal(id="d1", status="ACTIVE", closed_at=None, lot_id="l1", agreed_price=Decimal("100"), currency="CZK", price_base=Decimal("100"), delivery_method="x", payment_method="y")
    lot = Lot(id="l1", code="106")

    def mock_get(model, pk):
        if model == Deal and pk == "d1":
            return deal
        if model == Lot and pk == "l1":
            return lot
        return None

    session.get.side_effect = mock_get

    with patch("nextlife.api.routes_deal_screen.get_db_session") as mock_db:
        mock_db.return_value.__enter__.return_value = session
        resp = ui_cancel_deal("d1", authenticated=True)

        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/stock/106#sale"
        assert deal.status == "CANCELLED"
        assert deal.closed_at is not None


def test_stock_item_mobile_stepper_and_navigation_controls():
    """Stock item screen must render touch-friendly stepper pills, toolbar controls (Вернуться / Вперёд), and footer stage nav."""
    from starlette.requests import Request

    from nextlife.api.routes_stock import templates

    item = {
        "code": "130",
        "title": "AMD Radeon RX 7900 XTX",
        "status": "IN_STOCK",
        "status_badge_cls": "badge-ready",
        "description": "GPU description",
        "created_at": None,
        "target_price": Decimal("22000"),
        "target_currency": "CZK",
        "price_estimate_median": Decimal("21500"),
        "price_estimate_currency": "CZK",
        "storage_location": "Полка A-1",
        "photos": [],
        "listings": [],
        "listings_count": 0,
        "deal": None,
        "warranty_until": None,
    }

    req = Request({"type": "http", "method": "GET", "path": "/ui/stock/130", "headers": []})
    resp = templates.TemplateResponse(
        request=req,
        name="stock_item.html",
        context={"item": item, "it": item, "research": None, "listings": [], "deal": None, "photos": [], "approved_count": 0, "notice": None},
    )
    body = resp.body.decode("utf-8")

    # Stepper toolbar controls
    assert 'data-testid="btn-stepper-prev"' in body
    assert "Вернуться" in body
    assert 'data-testid="btn-stepper-next"' in body
    assert "Вперёд" in body
    assert 'data-testid="stepper-stage-indicator"' in body

    # Stepper pills
    assert 'data-testid="item-stepper"' in body
    assert 'data-testid="tab-price"' in body
    assert 'data-testid="tab-photos"' in body
    assert 'data-testid="tab-describe"' in body
    assert 'data-testid="tab-listings"' in body
    assert 'data-testid="tab-sale"' in body
    assert 'data-testid="tab-warranty"' in body

    # Stage footer navigation
    assert 'data-testid="btn-stage-footer-prev"' in body
    assert 'data-testid="btn-stage-footer-next"' in body


def test_stepper_centered_buttons_and_manual_price_only() -> None:
    """Verify stepper tabs are plain centered buttons without numbers/checkmarks,

    and that automated price median/range suggestions are removed.
    """
    item = {
        "code": "130",
        "title": "AMD Radeon RX 7900 XTX",
        "status": "NEW",
        "category_key": "gpu",
        "target_price": None,
        "target_currency": "CZK",
        "price_estimate_median": "19500",
        "price_estimate_currency": "CZK",
        "photos": [],
        "description": "",
        "listings": [],
        "deal": None,
        "warranty_until": None,
    }
    research = {
        "total_found": 12,
        "badge_text": "Live",
        "query": "RX 7900 XTX",
        "range": {"min": "18 000", "max": "24 000", "currency": "CZK"},
        "categories": ["Показать все", "Самостоятельный товар"],
        "links": [],
        "run": {"created_at": "2026-11-02 12:00"},
    }

    req = Request({"type": "http", "method": "GET", "path": "/ui/stock/130", "headers": []})
    resp = templates.TemplateResponse(
        request=req,
        name="stock_item.html",
        context={"item": item, "it": item, "research": research, "listings": [], "deal": None, "photos": [], "approved_count": 0, "notice": None},
    )
    body = resp.body.decode("utf-8")

    # Stepper buttons: centered alignment, no checkmark or circle numbers in tabs
    assert 'justify-content: center; text-align: center;' in body
    assert 'data-testid="tab-price"><span>Оценка</span></a>' in body.replace("\n", "").replace("  ", "")
    assert 'data-testid="tab-photos"><span>Фото</span></a>' in body.replace("\n", "").replace("  ", "")

    # Automated price estimate (median button and range) must be absent
    assert 'data-testid="btn-sell-median"' not in body
    assert "(диапазон:" not in body

    # Manual price input form must be present
    assert 'data-testid="input-custom-price"' in body
    assert 'data-testid="btn-save-price"' in body


