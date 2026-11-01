"""Unit tests for Stage 4 (Sklad: List, Filters, Bulk Mode, Quick Create)."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.services.stock import (
    bulk_update_location,
    get_stock_group_counts,
)
from nextlife.storage.models import Item


def test_quick_create_routes() -> None:
    """Verify Quick Create screen and dual-button submission logic (TZ §4.3)."""
    app = create_app()
    client = TestClient(app)
    cookies = {"nextlife_token": "test_token_secret"}

    # 1. GET /ui/stock/new renders 2 fields and 2 buttons
    r = client.get("/ui/stock/new", cookies=cookies)
    assert r.status_code == 200
    html = r.text
    assert 'data-testid="input-stock-title"' in html
    assert 'data-testid="input-stock-location"' in html
    assert 'data-testid="btn-create-submit"' in html
    assert 'data-testid="btn-create-and-next"' in html

    # 2. POST /api/stock with action="create" -> redirects to /ui/stock/{code}#price
    with patch("nextlife.api.routes_stock.create_stock_item", return_value={"code": "105"}):
        resp = client.post(
            "/api/stock",
            data={"title": "Тестовый предмет", "storage_location": "Полка A-1", "action": "create"},
            cookies=cookies,
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/stock/105#price"

    # 3. POST /api/stock with action="create_and_next" -> redirects to /ui/stock/new with location preserved
    with patch("nextlife.api.routes_stock.create_stock_item", return_value={"code": "106"}):
        resp_next = client.post(
            "/api/stock",
            data={"title": "Второй предмет", "storage_location": "Полка A-1", "action": "create_and_next"},
            cookies=cookies,
            follow_redirects=False,
        )
        assert resp_next.status_code == 303
        assert "/ui/stock/new?notice=created_next&created_code=106" in resp_next.headers["location"]
        from urllib.parse import unquote
        assert "location=Полка A-1" in unquote(resp_next.headers["location"])


def test_stock_list_and_filters_view() -> None:
    """Verify Sklad list elements: chips with counts, sort dropdown, and FAB '+' (TZ §4.1)."""
    app = create_app()
    client = TestClient(app)
    cookies = {"nextlife_token": "test_token_secret"}

    with patch("nextlife.api.routes_stock.list_stock", return_value=[]), \
         patch("nextlife.api.routes_stock.get_stock_group_counts", return_value={"all": 5, "new": 2, "prep": 1, "ready": 1, "on_sale": 1, "sold": 0, "archive": 0}):
        r = client.get("/ui/stock", cookies=cookies)
        assert r.status_code == 200
        html = r.text

        # 7 filter chips
        assert "Все активные (5)" in html
        assert "Новые (2)" in html
        assert "Подготовка (1)" in html
        assert "Готовы (1)" in html
        assert "На продаже (1)" in html
        assert "Проданы (0)" in html
        assert "Архив (0)" in html

        # Sort dropdown
        assert 'data-testid="stock-sort"' in html
        assert "Недавно изменённые" in html
        assert "Дольше всех на складе" in html

        # View mode toggle
        assert 'data-testid="btn-view-toggle"' in html

        # Floating Action Button '+'
        assert 'data-testid="fab-add"' in html
        assert 'href="/ui/stock/new"' in html

        # Bulk toolbar
        assert 'data-testid="bulk-toolbar"' in html


def test_bulk_location_and_estimate() -> None:
    """Verify bulk operations: changing location and price estimation (TZ §4.2)."""
    session = MagicMock()
    item1 = MagicMock(spec=Item)
    item1.code = "101"
    item1.title = "Item 1"
    item1.storage_location = "Old A"
    item1.description = "Desc"
    item1.specs = None
    item1.notes = None
    item1.attrs = None

    item2 = MagicMock(spec=Item)
    item2.code = "102"
    item2.title = "Item 2"
    item2.storage_location = "Old B"
    item2.description = None
    item2.specs = None
    item2.notes = None
    item2.attrs = None

    session.get.side_effect = lambda model, code: item1 if code == "101" else (item2 if code == "102" else None)

    updated = bulk_update_location(session, ["101", "102"], "New Shelf C")
    assert updated == 2
    assert item1.storage_location == "New Shelf C"
    assert item2.storage_location == "New Shelf C"
    assert "new shelf c" in item1.search_text


def test_group_counts_calculation() -> None:
    """Verify get_stock_group_counts maps statuses to 7 groups correctly."""
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [
        "NEW",
        "ESTIMATED",
        "PHOTOGRAPHED",
        "READY_FOR_SALE",
        "PUBLISHED",
        "SOLD",
        "ARCHIVED",
    ]
    counts = get_stock_group_counts(session)
    assert counts["new"] == 2  # NEW + ESTIMATED
    assert counts["prep"] == 1  # PHOTOGRAPHED
    assert counts["ready"] == 1  # READY_FOR_SALE
    assert counts["on_sale"] == 1  # PUBLISHED
    assert counts["sold"] == 1  # SOLD
    assert counts["archive"] == 1  # ARCHIVED
    assert counts["all"] == 5  # NEW, ESTIMATED, PHOTOGRAPHED, READY_FOR_SALE, PUBLISHED
