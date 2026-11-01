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
