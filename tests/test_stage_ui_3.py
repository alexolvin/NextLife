"""Unit and integration tests for Stage 3 (Navigation, Header, Bottom Bar, Search, Redirects)."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.services.search import search_unified
from nextlife.storage.models import Item, Listing, Message


def test_navigation_redirects() -> None:
    """Verify 301 permanent redirects specified in TZ §3.4."""
    app = create_app()
    client = TestClient(app)
    cookies = {"nextlife_token": "test_token_secret"}

    # 1. /ui/dashboard -> /ui/attention
    r = client.get("/ui/dashboard", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/attention"

    # 2. /ui/inbox -> /ui/messages
    r = client.get("/ui/inbox", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/messages"

    # 3. /ui/archive -> /ui/stock?group=archive
    r = client.get("/ui/archive", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/stock?group=archive"

    # 4. /ui/lot-wizard -> /ui/stock
    r = client.get("/ui/lot-wizard", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/stock"

    # 5. /ui/lot-wizard/item -> /ui/stock/new
    r = client.get("/ui/lot-wizard/item", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/stock/new"

    # 6. /ui/lot-wizard/text?item_code=123 -> /ui/stock/123#describe
    r = client.get("/ui/lot-wizard/text?item_code=123", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/stock/123#describe"

    # 7. /ui/lot-wizard/finalize?item_code=123 -> /ui/stock/123#listings
    r = client.get("/ui/lot-wizard/finalize?item_code=123", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/stock/123#listings"

    # 8. /ui/lot-wizard/publish?item_code=123 -> /ui/stock/123#listings
    r = client.get("/ui/lot-wizard/publish?item_code=123", cookies=cookies, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/ui/stock/123#listings"


def test_base_navigation_elements() -> None:
    """Verify header and bottom nav structure in rendered HTML per TZ §3.1 and §3.2."""
    app = create_app()
    client = TestClient(app)
    cookies = {"nextlife_token": "test_token_secret"}

    mock_metrics = {
        "awaiting_approval": {"count": 0, "link": "/ui/stock"},
        "escalated_messages": {"count": 0, "link": "/ui/messages"},
        "expiring_reservations": {"count": 0, "link": "/ui/stock"},
        "stale_lots": {"count": 0, "link": "/ui/listings"},
        "pending_shipments": {"count": 0, "link": "/ui/stock"},
        "pending_payment_confirmations": {"count": 0, "link": "/ui/stock"},
    }

    # Mock dashboard metrics so /ui/attention can render without live DB query failure
    with patch("nextlife.api.routes_dashboard.get_dashboard_metrics", return_value=mock_metrics):
        r = client.get("/ui/attention", cookies=cookies)
        assert r.status_code == 200
        html = r.text

        # Header right icons: Search, Tasks, Settings
        assert 'data-testid="header-search"' in html
        assert 'data-testid="header-tasks"' in html
        assert 'data-testid="header-settings"' in html

        # Forbidden old elements:
        assert 'id="btn-refresh"' not in html
        assert "Обновить данные" not in html
        assert "Мастер лота" not in html

        # Bottom nav items (5 items)
        assert 'data-testid="nav-attention"' in html
        assert 'data-testid="nav-stock"' in html
        assert 'data-testid="nav-listings"' in html
        assert 'data-testid="nav-messages"' in html
        assert 'data-testid="nav-buy"' in html

        # Removed from bottom bar:
        assert 'data-testid="nav-create"' not in html
        assert 'data-testid="nav-menu"' not in html
        assert 'id="menu-overlay"' not in html


def test_unified_search_service() -> None:
    """Verify unified search scoring, diacritic/case normalization, and scopes."""
    session = MagicMock()

    item1 = MagicMock(spec=Item)
    item1.code = "101"
    item1.title = "Stříbrný iPhone 13 Pro"
    item1.search_text = "101 #101 stribrny iphone 13 pro apple telefon"
    item1.status = "READY_FOR_SALE"
    item1.storage_location = "A-1"
    item1.price_estimate_median = 12000

    item2 = MagicMock(spec=Item)
    item2.code = "102"
    item2.title = "Pouzdro pro iPhone 13"
    item2.search_text = "102 #102 pouzdro pro iphone 13 prislusenstvi"
    item2.status = "NEW"
    item2.storage_location = "B-2"
    item2.price_estimate_median = 300

    listing1 = MagicMock(spec=Listing)
    listing1.id = "l-1"
    listing1.item_code = "101"
    listing1.title = "iPhone 13 Pro 128GB Stříbrný"
    listing1.description = "Top stav, bez záruky"
    listing1.marketplace_key = "bazos_cz"
    listing1.status = "PUBLISHED"
    listing1.price = 12500
    listing1.currency = "CZK"

    msg1 = MagicMock(spec=Message)
    msg1.id = "m-1"
    msg1.thread_id = "t-1"
    msg1.text_original = "Dobrý den, je ten iPhone ještě volný?"
    msg1.text_ru = "Здравствуйте, этот iPhone еще свободен?"
    msg1.direction = "IN"
    msg1.sent_at = None

    # Configure session mock queries
    def mock_scalars(stmt):
        mock_result = MagicMock()
        s_str = str(stmt).lower()
        if "from item" in s_str:
            mock_result.all.return_value = [item1, item2]
        elif "from listing" in s_str:
            mock_result.all.return_value = [listing1]
        elif "from message" in s_str:
            mock_result.all.return_value = [msg1]
        else:
            mock_result.all.return_value = []
        return mock_result

    session.scalars.side_effect = mock_scalars

    # Search with diacritics and upper/lowercase
    res = search_unified(session, query="stribrny iphone", scope="all")
    assert res["total"] >= 1
    assert len(res["items"]) >= 1
    assert res["items"][0]["code"] == "101"

    # Search with scope='items'
    res_items = search_unified(session, query="iphone", scope="items")
    assert res_items["scope"] == "items"
    assert len(res_items["items"]) == 2
    assert len(res_items["listings"]) == 0

    # Search with code '#101'
    res_code = search_unified(session, query="#101", scope="items")
    assert res_code["items"][0]["score"] == 100
