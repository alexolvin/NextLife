"""Test suite for Stage 8: messages, notifications and notion integration (TZ §7)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from nextlife.services.messages import (
    send_reply,
    suggest_reply,
)
from nextlife.services.notion import (
    NOTION_API_VERSION,
    create_or_update_schedule_event,
)
from nextlife.storage.models import Deal, Item, Listing, Lot, OwnerNotification, Thread


def test_openclaw_notifications_endpoints():
    from nextlife.api.main import create_app
    from nextlife.api.routes_openclaw import check_openclaw_auth

    app = create_app()
    app.dependency_overrides[check_openclaw_auth] = lambda: True
    client = TestClient(app)

    notif_id = str(uuid.uuid4())
    mock_notif = OwnerNotification(
        id=notif_id,
        kind="NEW_BUYER_MESSAGE",
        title="Новое сообщение от покупателя",
        body="Здравствуйте, товар ещё продаётся?",
        url="/ui/messages/th-1",
        delivered_at=None,
    )

    with patch("nextlife.api.routes_openclaw_ops.get_db_session") as mock_session_ctx:
        session = MagicMock()
        session.scalars.return_value.all.return_value = [mock_notif]
        session.get.return_value = mock_notif
        mock_session_ctx.return_value.__enter__.return_value = session

        # 1. Pull undelivered notifications
        resp = client.get("/api/oc/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == notif_id
        assert data[0]["kind"] == "NEW_BUYER_MESSAGE"

        # 2. Ack notification
        ack_resp = client.post(f"/api/oc/notifications/{notif_id}/ack")
        assert ack_resp.status_code == 200
        ack_data = ack_resp.json()
        assert ack_data["status"] == "ok"
        assert ack_data["id"] == notif_id
        assert mock_notif.delivered_at is not None


def test_notion_integration_create_event():
    session = MagicMock()
    mock_deal = Deal(
        id="deal-1",
        lot_id="lot-1",
        closure_evidence={},
    )
    mock_lot = MagicMock()
    mock_lot.item_id = "ITM-99"
    mock_item = Item(code="ITM-99", title="Ноутбук Lenovo")
    session.get.side_effect = lambda model, pk: (
        mock_deal if model == Deal else (mock_lot if model == Lot else mock_item)
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "page-notion-123"}

    config = {
        "token": "secret_notion_token",
        "database_id": "db_notion_123",
        "title_prop": "Name",
        "date_prop": "Date",
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post, patch("nextlife.services.notion.get_notion_config", return_value=config):
        res = create_or_update_schedule_event(
            session=session,
            deal_id="deal-1",
            event_type="shipment",
            dt_iso="2026-11-05T14:00:00Z",
            location_or_carrier="Zásilkovna",
            note="Отправить до 15:00",
        )
        assert res["status"] == "ok"
        assert res["page_id"] == "page-notion-123"
        assert "Отправка #ITM-99" in res["event_title"]

        # Verify header Notion-Version: 2022-06-28
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["headers"]["Notion-Version"] == NOTION_API_VERSION
        assert kwargs["headers"]["Authorization"] == "Bearer secret_notion_token"


def test_suggest_and_send_reply():
    session = MagicMock()
    mock_thread = Thread(
        id="th-test",
        listing_id="lst-1",
        channel="bazos_cz",
        counterparty="Petr",
        lang="cs",
    )
    mock_item = Item(code="ITM-10", title="Наушники Sony", warranty_months=12)
    mock_listing = Listing(id="lst-1", item_code="ITM-10", marketplace_key="bazos_cz")

    session.get.side_effect = lambda model, pk: (
        mock_thread if model == Thread else (mock_listing if model == Listing else mock_item)
    )

    # Suggest reply
    suggestion = suggest_reply(session, "th-test")
    assert "Dobrý den" in suggestion["reply_original"]
    assert "Здравствуйте" in suggestion["reply_ru"]

    # Send reply
    with patch("nextlife.services.messages.get_marketplace_capabilities", return_value={"messages_send": True}):
        res = send_reply(session, "th-test", text_ru="Добрый день, товар в наличии")
        assert res["status"] == "ok"
        assert res["supported"] is True
        session.add.assert_called()


def test_messages_screen_routes():
    from nextlife.api.main import create_app
    from nextlife.api.routes import check_auth

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    with patch("nextlife.api.routes_messages.get_db_session") as mock_session_ctx:
        session = MagicMock()
        mock_session_ctx.return_value.__enter__.return_value = session

        with patch("nextlife.api.routes_messages.list_threads", return_value=[]):
            resp = client.get("/ui/messages")
            assert resp.status_code == 200
            assert "Сообщения" in resp.text
            assert 'data-testid="messages-screen"' in resp.text
            assert 'data-testid="chip-unread"' in resp.text


def test_no_forbidden_words_in_stage_8_templates():
    forbidden = ["лот", "мастер создания лота", "мастер лота", "dry-run", "фрейм"]
    files = [
        Path("templates/web/messages.html"),
        Path("templates/web/message_detail.html"),
    ]
    for f in files:
        if f.exists():
            content = f.read_text(encoding="utf-8").lower()
            for word in forbidden:
                assert word not in content, f"Forbidden word '{word}' found in {f}"
