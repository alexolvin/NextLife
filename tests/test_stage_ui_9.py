"""Test suite for Stage 9: buy section and kit analysis (TZ §8)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from nextlife.services.buy import (
    analyze_kit,
    create_buy_search,
    format_candidates,
)
from nextlife.storage.models import BuyCandidate, BuyRequest


def test_create_buy_search_and_classification():
    session = MagicMock()
    with patch("nextlife.services.buy.create_job") as mock_job:
        req = create_buy_search(session, query="iPhone 13", currency="CZK")
        assert req.query == "iPhone 13"
        assert req.currency == "CZK"
        assert session.add.call_count >= 1
        mock_job.assert_called_once()


def test_format_candidates_filtering_and_sort():
    c1 = BuyCandidate(
        id="c1",
        request_id="r1",
        marketplace_key="bazos_cz",
        price=Decimal("1000"),
        currency="CZK",
        verdict={"condition": "used", "offer_type": "single", "total_price": "1079"},
    )
    c2 = BuyCandidate(
        id="c2",
        request_id="r1",
        marketplace_key="sbazar_cz",
        price=Decimal("2000"),
        currency="CZK",
        verdict={"condition": "new", "offer_type": "kit", "total_price": "2079"},
    )

    all_res = format_candidates([c1, c2], condition_filter="all", type_filter="all")
    assert len(all_res) == 2

    # Filter condition=new
    new_res = format_candidates([c1, c2], condition_filter="new", type_filter="all")
    assert len(new_res) == 1
    assert new_res[0]["id"] == "c2"

    # Filter offer_type=single
    single_res = format_candidates([c1, c2], condition_filter="all", type_filter="single")
    assert len(single_res) == 1
    assert single_res[0]["id"] == "c1"


def test_analyze_kit_profitability():
    session = MagicMock()
    mock_candidate = BuyCandidate(
        id="cand-kit-1",
        request_id="r-kit",
        marketplace_key="bazos_cz",
        price=Decimal("1500"),
        currency="CZK",
        verdict={"title_ru": "Комплект фототехники", "total_price": "1579"},
    )
    session.get.return_value = mock_candidate

    analysis = analyze_kit(session, "cand-kit-1")
    assert "components" in analysis
    assert len(analysis["components"]) >= 2
    assert analysis["parts_sum"] > 0
    # Profit calculation: (parts_sum - total_cost) / parts_sum * 100
    assert analysis["profit_percent"] > 0
    # Notification added if >= 25% profit
    assert session.add.called


def test_buy_screens_routes():
    from nextlife.api.main import create_app
    from nextlife.api.routes import check_auth

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    with patch("nextlife.api.routes_buy.get_db_session") as mock_session_ctx:
        session = MagicMock()
        mock_req = BuyRequest(id="req-1", query="Playstation 4", currency="CZK")
        mock_candidate = BuyCandidate(
            id="cand-1",
            request_id="req-1",
            marketplace_key="bazos_cz",
            price=Decimal("2000"),
            currency="CZK",
            verdict={"kit_analysis": {
                "title": "Комплект PS4",
                "components": [],
                "parts_sum": 3000,
                "total_cost": "2079",
                "profit_percent": 30.7,
            }},
        )
        session.scalars.return_value.all.side_effect = [[mock_req], [mock_candidate]]
        session.get.side_effect = lambda model, pk: mock_req if model == BuyRequest else mock_candidate
        mock_session_ctx.return_value.__enter__.return_value = session

        # 1. Main Buy screen
        resp = client.get("/ui/buy")
        assert resp.status_code == 200
        assert "Покупки" in resp.text
        assert 'data-testid="buy-screen"' in resp.text
        assert 'data-testid="buy-query-input"' in resp.text

        # 2. Kit analysis screen
        kit_resp = client.get("/ui/buy/kits/cand-1")
        assert kit_resp.status_code == 200
        assert "Анализ выгодности комплекта" in kit_resp.text
        assert 'data-testid="kit-analysis-screen"' in kit_resp.text
        assert 'data-testid="val-profit-percent"' in kit_resp.text


def test_no_forbidden_words_in_stage_9_templates():
    forbidden = ["лот", "мастер создания лота", "мастер лота", "dry-run", "фрейм"]
    files = [
        Path("templates/web/buy.html"),
        Path("templates/web/buy_kit.html"),
    ]
    for f in files:
        if f.exists():
            content = f.read_text(encoding="utf-8").lower()
            for word in forbidden:
                assert word not in content, f"Forbidden word '{word}' found in {f}"
