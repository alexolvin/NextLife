"""Unit and integration tests for Stage 10 (Attention, Settings, OpenClaw gateway, Legacy cleanup).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.api.routes import check_auth
from nextlife.api.routes_openclaw import check_openclaw_auth


@pytest.fixture
def app():
    application = create_app()
    application.dependency_overrides[check_auth] = lambda: True
    application.dependency_overrides[check_openclaw_auth] = lambda: True
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_ui_attention_page(client):
    """Attention page renders successfully (TZ §10)."""
    with patch("nextlife.api.routes_dashboard.get_db_session") as mock_db, \
         patch("nextlife.api.routes_dashboard.get_attention_cards", return_value=[]):
        mock_db.return_value.__enter__.return_value = MagicMock()
        resp = client.get("/ui/attention")
        assert resp.status_code == 200
        assert "Внимание" in resp.text
        assert "Всё спокойно" in resp.text


def test_ui_settings_tabs(client):
    """Settings page renders each of the 5 tabs (TZ §11)."""
    for tab in ("profile", "marketplaces", "ai", "notion", "buy"):
        resp = client.get(f"/ui/settings?tab={tab}")
        assert resp.status_code == 200
        assert "Настройки" in resp.text
        if tab == "marketplaces":
            assert "Матрица возможностей площадок" in resp.text


def test_ui_settings_post_actions(client, tmp_path):
    """Settings form submissions save successfully."""
    with patch("nextlife.services.llm_filter.get_data_dir", return_value=tmp_path):
        # Profile
        resp = client.post(
            "/ui/settings/profile",
            data={
                "seller_name": "Test Seller",
                "seller_psc": "120 00",
                "seller_phone": "+420123456789",
                "default_meeting_place": "Prague Main Station",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "tab=profile" in resp.headers["location"]

        # AI
        resp = client.post(
            "/ui/settings/ai",
            data={
                "llm_endpoint": "https://api.openai.com/v1",
                "llm_api_key": "sk-test",
                "llm_model": "gpt-4o-mini",
                "image_ai_endpoint": "https://api.openai.com/v1/images/edits",
                "image_ai_api_key": "sk-image",
                "image_ai_model": "dall-e-2",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "tab=ai" in resp.headers["location"]

        # Notion
        resp = client.post(
            "/ui/settings/notion",
            data={
                "notion_token": "secret_abc",
                "notion_database_id": "db_123",
                "notion_title_prop": "Name",
                "notion_date_prop": "Date",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "tab=notion" in resp.headers["location"]

        # Buy
        resp = client.post(
            "/ui/settings/buy",
            data={"default_currency": "EUR", "profit_threshold_pct": "30"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "tab=buy" in resp.headers["location"]


def test_ui_settings_test_buttons(client):
    """Test buttons on AI and Notion sections."""
    resp = client.post("/ui/settings/test-llm", follow_redirects=False)
    assert resp.status_code == 303

    resp = client.post("/ui/settings/test-image-ai", follow_redirects=False)
    assert resp.status_code == 303

    resp = client.post("/ui/settings/test-notion", follow_redirects=False)
    assert resp.status_code == 303


def test_openclaw_auth_protection():
    """OpenClaw endpoints require auth when overrides are removed."""
    clean_app = create_app()
    clean_client = TestClient(clean_app)

    resp = clean_client.get("/api/oc/listings")
    assert resp.status_code == 401

    resp = clean_client.post("/api/oc/stock/NL-TEST/ready")
    assert resp.status_code == 401


def test_openclaw_stock_endpoints(client):
    """OpenClaw stock endpoints (price-research and ready)."""
    # 404 for missing item
    with patch("nextlife.api.routes_openclaw_v2.get_db_session") as mock_db:
        session = MagicMock()
        session.get.return_value = None
        mock_db.return_value.__enter__.return_value = session

        resp = client.get("/api/oc/stock/NONEXISTENT/price-research")
        assert resp.status_code == 404

        resp = client.post("/api/oc/stock/NONEXISTENT/ready")
        assert resp.status_code == 404

    # 409 when checklist unmet
    with patch("nextlife.api.routes_openclaw_v2.get_db_session") as mock_db:
        session = MagicMock()
        mock_item = MagicMock()
        mock_item.code = "NL-001"
        mock_item.target_price = None
        mock_item.title = ""
        mock_item.description = ""
        mock_item.shipping_policy = None
        session.get.return_value = mock_item
        session.scalars.return_value.all.return_value = []
        mock_db.return_value.__enter__.return_value = session

        resp = client.post("/api/oc/stock/NL-001/ready")
        assert resp.status_code == 409
        assert "Checklist not met" in resp.text


def test_openclaw_listings_endpoints(client):
    """OpenClaw listings endpoints."""
    with patch("nextlife.api.routes_openclaw_v2.get_db_session") as mock_db:
        session = MagicMock()
        mock_lst = MagicMock()
        mock_lst.id = 1
        mock_lst.item_code = "NL-001"
        mock_lst.marketplace_key = "bazos_cz"
        mock_lst.title = "Test Item"
        mock_lst.price = 1000
        mock_lst.currency = "CZK"
        mock_lst.status = "DRAFT"
        mock_lst.publish_evidence = {}
        session.scalars.return_value.all.return_value = [mock_lst]
        session.get.return_value = mock_lst
        mock_db.return_value.__enter__.return_value = session

        # List
        resp = client.get("/api/oc/listings")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Item"

        # Detail
        resp = client.get("/api/oc/listings/1")
        assert resp.status_code == 200

        # Patch
        resp = client.patch("/api/oc/listings/1", json={"title": "Updated Title"})
        assert resp.status_code == 200

        # Approve
        with patch("nextlife.api.routes_openclaw_v2.approve_listing") as mock_app:
            resp = client.post("/api/oc/listings/1/approve")
            assert resp.status_code == 200
            assert mock_app.called

        # Unapprove
        with patch("nextlife.api.routes_openclaw_v2.unapprove_listing") as mock_unapp:
            resp = client.post("/api/oc/listings/1/unapprove")
            assert resp.status_code == 200
            assert mock_unapp.called


def test_openclaw_messages_and_buy_endpoints(client):
    """OpenClaw messages and buy endpoints."""
    with patch("nextlife.api.routes_openclaw_v2.get_db_session") as mock_db:
        session = MagicMock()
        mock_db.return_value.__enter__.return_value = session

        with patch("nextlife.api.routes_openclaw_v2.list_threads", return_value=[]):
            resp = client.get("/api/oc/messages")
            assert resp.status_code == 200
            assert resp.json() == []

        with patch("nextlife.api.routes_openclaw_v2.suggest_reply", return_value="Здравствуйте!"):
            resp = client.post("/api/oc/messages/1/suggest")
            assert resp.status_code == 200
            assert resp.json()["suggestion"] == "Здравствуйте!"

        mock_msg = MagicMock()
        mock_msg.id = 42
        with patch("nextlife.api.routes_openclaw_v2.send_reply", return_value=mock_msg):
            resp = client.post("/api/oc/messages/1/send", json={"text": "Привет!"})
            assert resp.status_code == 200
            assert resp.json()["message_id"] == 42

        mock_req = MagicMock(id=10)
        mock_job = MagicMock(id="job-100")
        with patch("nextlife.api.routes_openclaw_v2.create_buy_search", return_value=(mock_req, mock_job)):
            resp = client.post("/api/oc/buy/search", json={"query": "ThinkPad"})
            assert resp.status_code == 200
            assert resp.json()["request_id"] == 10


def test_scanner_no_forbidden_words_in_templates():
    """Scanner verifying zero occurrences of forbidden words across all rendered UI templates (§1 п.1).

    Forbidden: 'Лот', 'Мастер лота', 'Мастер создания лота', 'фрейм', 'dry-run', 'DRY_RUN_OK'.
    """
    templates_dir = Path(__file__).resolve().parent.parent / "templates" / "web"
    pattern = re.compile(
        r"\b[Лл]от(а|у|ом|е|ы|ов|ам|ами|ах)?\b|\b[Фф]рейм(а|у|ом|е|ы|ов|ам|ами|ах)?\b|dry-run|DRY_RUN_OK|Мастер лота|Мастер создания лота"
    )

    violations = []
    for html_file in templates_dir.glob("*.html"):
        text = html_file.read_text(encoding="utf-8")
        matches = pattern.findall(text)
        if matches:
            violations.append(f"{html_file.name}: {matches}")

    assert not violations, f"Forbidden words found in templates: {violations}"
