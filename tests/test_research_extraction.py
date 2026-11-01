"""Unit tests for research candidate extraction and template rendering."""

from decimal import Decimal
from unittest.mock import MagicMock

from nextlife.services.research import build_research_panel, extract_search_candidates


def test_extract_search_candidates():
    # Long real item title with marketing words and memory specs
    title = "ASUS TUF Gaming AMD Radeon RX 7900 XTX 24 GB"
    candidates = extract_search_candidates(title)

    assert "RX 7900 XTX" in candidates
    assert "Radeon RX 7900 XTX" in candidates
    # Full title must be first candidate
    assert candidates[0] == title


def test_extract_search_candidates_simple():
    title = "PlayStation 5 Slim 1TB"
    candidates = extract_search_candidates(title)
    assert candidates[0] == title
    assert any("PlayStation 5" in c or "PS5" in c for c in candidates)


def test_build_research_panel_structure():
    session = MagicMock()
    # Mock item
    item = MagicMock()
    item.code = "130"
    item.title = "ASUS TUF Gaming AMD Radeon RX 7900 XTX 24 GB"
    item.target_price = Decimal("21000")
    item.target_currency = "CZK"
    item.price_estimate_median = Decimal("21500")
    item.price_estimate_low = Decimal("19000")
    item.price_estimate_high = Decimal("24000")
    item.price_estimate_currency = "CZK"

    # Mock run
    mock_run = MagicMock()
    mock_run.id = "run-1"
    mock_run.status = "SUCCESS"
    mock_run.marketplaces = {"query": "RX 7900 XTX"}
    mock_run.error = {}
    mock_run.created_at = None
    mock_run.finished_at = None

    session.execute.return_value.scalars.return_value.first.return_value = mock_run
    session.execute.return_value.scalars.return_value.all.return_value = []

    config = MagicMock()
    config.app.min_comparables = 5
    panel = build_research_panel(session, item, config)

    assert panel is not None
    assert "query" in panel
    assert panel["query"] == "RX 7900 XTX"
    assert panel["total_found"] == 0
    assert panel["links"] == []


def test_stock_item_template_renders_research():
    from starlette.requests import Request

    from nextlife.api.routes import templates

    item = {
        "code": "130",
        "title": "ASUS TUF Gaming AMD Radeon RX 7900 XTX 24 GB",
        "status": "IN_STOCK",
        "status_badge_cls": "badge-ready",
        "description": "Top tier GPU",
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

    research = {
        "status": "SUCCESS",
        "badge_class": "badge-ready",
        "badge_text": "Оценка готова",
        "total_found": 14,
        "marketplaces_scanned": ["bazos_cz", "sbazar_cz", "aukro_cz"],
        "range": {"min": 19000, "max": 24000, "currency": "CZK"},
        "links": [
            {
                "marketplace": "bazos_cz",
                "title": "RX 7900 XTX Nitro+ 24GB",
                "url": "https://pc.bazos.cz/inzerat/123",
                "price": 20500,
                "currency": "CZK",
            }
        ],
        "query": "RX 7900 XTX",
        "run": {"id": "test-run", "created_at": None, "status": "SUCCESS"},
    }

    req = Request({"type": "http", "method": "GET", "path": "/ui/stock/130", "headers": []})
    resp = templates.TemplateResponse(
        request=req,
        name="stock_item.html",
        context={
            "item": item,
            "it": item,
            "research": research,
            "listings": [],
            "deal": None,
            "notice": "research_done",
            "photos": [],
        },
    )
    body = resp.body.decode("utf-8")
    assert "Этап 1. Оценка цены аналогов" in body
    assert "RX 7900 XTX Nitro+ 24GB" in body
    assert "20500 CZK" in body
    assert "https://pc.bazos.cz/inzerat/123" in body
    assert "Найдено предложений: 14" in body
    assert "Поиск аналогов на площадках завершён" in body


def test_stock_item_template_renders_empty_research():
    from starlette.requests import Request

    from nextlife.api.routes import templates

    item = {
        "code": "130",
        "title": "Rare Item",
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
        "photos": [],
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

    # Case 1: research is None (not yet run)
    req = Request({"type": "http", "method": "GET", "path": "/ui/stock/130", "headers": []})
    resp = templates.TemplateResponse(
        request=req,
        name="stock_item.html",
        context={"item": item, "it": item, "research": None, "listings": [], "deal": None, "notice": None, "photos": []},
    )
    body = resp.body.decode("utf-8")
    assert "Нажмите кнопку «Найти аналоги»" in body

    # Case 2: research ran, 0 results
    research_zero = {
        "status": "SUCCESS",
        "badge_class": "badge-ready",
        "badge_text": "0 предложений",
        "total_found": 0,
        "marketplaces_scanned": ["bazos_cz", "sbazar_cz", "aukro_cz"],
        "range": None,
        "links": [],
        "query": "Rare Item",
        "run": {"id": "test-run", "created_at": None, "status": "SUCCESS"},
    }
    resp2 = templates.TemplateResponse(
        request=req,
        name="stock_item.html",
        context={"item": item, "it": item, "research": research_zero, "listings": [], "deal": None, "notice": None, "photos": []},
    )
    body2 = resp2.body.decode("utf-8")
    assert "предложений на площадках пока не найдено" in body2


def test_fx_rate_fallback_when_db_empty():
    """get_latest_rate_prior_or_on must return baseline fallback rate when fx_rate table is empty."""
    from datetime import date

    from nextlife.money.money import get_latest_rate_prior_or_on

    session = MagicMock()
    # Mock empty query results
    session.execute.return_value.first.return_value = None

    rate, r_date = get_latest_rate_prior_or_on(session, "CZK", date(2026, 11, 1))
    assert rate == Decimal("25.0")
    assert r_date == date(2026, 11, 1)

    rate_pln, _ = get_latest_rate_prior_or_on(session, "PLN", date(2026, 11, 1))
    assert rate_pln == Decimal("4.30")
