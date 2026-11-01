"""Tests for Stage E7 (Web-UI)."""

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.conftest import clean_database

from nextlife.api.main import create_app
from nextlife.core.status import ItemStatus
from nextlife.services.sell import build_draft
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item


def create_test_item(session: Session, code: str) -> Item:
    item = Item(
        code=code,
        kind="SELL",
        title=f"Vintage Lens {code}",
        category_key="electronics",
        attrs={"pickup_allowed": True},
        condition_key="used",
        defects={},
        status=ItemStatus.AWAITING_APPROVAL.value,
        target_price=Decimal("120.00"),
        target_currency="EUR",
    )
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def clean_db_session() -> Session:
    with get_db_session() as session:
        clean_database(session)
        yield session
        clean_database(session)


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_unauthenticated_access_is_blocked(client: TestClient) -> None:
    # Requests without token must be rejected with 401 Unauthorized
    resp = client.get("/ui/items")
    assert resp.status_code == 401
    assert "Unauthorized" in resp.json()["detail"]

    resp = client.get("/ui/inbox")
    assert resp.status_code == 401


def test_authenticated_screens_render_360px_mobile_layout(
    client: TestClient, clean_db_session: Session
) -> None:
    session = clean_db_session
    code = "701"
    create_test_item(session, code)

    token = os.environ.get("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Feed screen
    resp = client.get("/ui/items", headers=headers)
    assert resp.status_code == 200
    assert f"#{code}" in resp.text
    # N2: min-width мобильной вёрстки больше не инлайн-<style> в base.html,
    # а токенизированный контракт в app.css (--app-min-w)
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "--app-min-w" in css.text
    assert "min-width: var(--app-min-w)" in css.text

    # 2. Item card screen
    resp = client.get(f"/ui/items/{code}", headers=headers)
    assert resp.status_code == 200
    # TZ-NextLife-UI-N2 V4 (P-E13): лента аудита — служебный свёрнутый блок.
    assert "лента событий" in resp.text

    # 3. Draft approval screen
    resp = client.get(f"/ui/draft/{code}", headers=headers)
    assert resp.status_code == 200
    assert "Утверждение черновика" in resp.text

    # 4. Inbox screen
    resp = client.get("/ui/inbox", headers=headers)
    assert resp.status_code == 200
    assert "Входящие сообщения" in resp.text

    # 5. Deals & archive screen
    resp = client.get("/ui/archive", headers=headers)
    assert resp.status_code == 200
    assert "Архив предметов" in resp.text


def test_draft_approval_via_web_ui(client: TestClient, clean_db_session: Session) -> None:
    session = clean_db_session
    code = "702"
    create_test_item(session, code)

    token = os.environ.get("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    headers = {"Authorization": f"Bearer {token}"}

    draft = build_draft(session, code)
    payload_hash = draft["payload_hash"]

    # Submit approval through Web API endpoint
    resp = client.post(
        f"/ui/draft/{code}/approve",
        params={"payload_hash": payload_hash},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    # Verify item is marked APPROVED in database
    item = session.get(Item, code)
    assert item is not None
    assert item.status == ItemStatus.APPROVED.value
