"""Tests for Buy search budget handling and Vinted connector search."""

from decimal import Decimal
import pytest
from nextlife.core.config import load_config
from nextlife.connectors.registry import get_connector
from nextlife.connectors.vinted import VintedConnector, parse_vinted_catalog
from nextlife.services.buy import create_buy_search, format_candidates
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BuyRequest, BuyCandidate


def test_buy_search_without_budget_creates_valid_request():
    """Verify bug fix 2bef8d93b13a: search without budget works cleanly with NOT NULL constraint relaxed."""
    with get_db_session() as session:
        req = create_buy_search(
            session=session,
            query="Ddr4 32",
            currency="CZK",
            postal_code=None,
            budget=None,
        )
        session.commit()

        assert req.id is not None
        assert req.query == "Ddr4 32"
        assert req.budget == Decimal("0.00")
        assert len(req.candidates) > 0

        # Verify formatting
        rows = format_candidates(req.candidates)
        assert len(rows) > 0
        first = rows[0]
        assert "title_ru" in first
        assert "price" in first
        assert "total_price" in first
        assert "external_url" in first


def test_buy_search_with_budget():
    """Verify search with explicit budget saves budget properly."""
    with get_db_session() as session:
        req = create_buy_search(
            session=session,
            query="Apple iPhone",
            currency="CZK",
            postal_code="11000",
            budget=Decimal("12000.00"),
        )
        session.commit()

        assert req.budget == Decimal("12000.00")
        assert req.attrs.get("postal_code") == "11000"


def test_vinted_connector_search_returns_real_items():
    """Verify Vinted web connector returns real listings from vinted.cz."""
    cfg = load_config()
    connector = get_connector("vinted", cfg)
    assert isinstance(connector, VintedConnector)

    # Search with limit
    results = connector.search("ram ddr4", limit=5)
    assert len(results) > 0
    item = results[0]
    assert item.title
    assert item.price >= Decimal("0")
    assert item.currency in ("CZK", "EUR", "PLN")
    assert item.external_url.startswith("https://www.vinted.cz/items/")
    assert item.raw.get("external_id")


def test_prune_past_buy_requests_depth_7():
    """Verify auto-deletion of past buy searches maintaining depth of 7."""
    from nextlife.services.buy import prune_past_buy_requests
    import uuid

    with get_db_session() as session:
        # Add 10 dummy requests
        for i in range(10):
            r = BuyRequest(
                id=str(uuid.uuid4()),
                query=f"Dummy search {i}",
                budget=Decimal("0.00"),
                currency="CZK",
                status="COMPLETED",
            )
            session.add(r)
        session.commit()

        # Prune to depth 7
        pruned = prune_past_buy_requests(session, keep_depth=7)
        session.commit()

        assert pruned >= 3
        count = session.query(BuyRequest).count()
        assert count == 7
