"""Tests for Dashboard six 'requires attention' blocks with real live counts (T-045).

TZ v2.0 Section V.2 item 1:
- Six blocks: awaiting approval, escalated messages, expiring reservations,
  stale lots, pending shipments, pending payment confirmations.
- Real live counts.
- Each block is clickable and leads to a filtered list.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.core.secrets import resolve_secret
from nextlife.core.status import ItemStatus
from nextlife.services.dashboard import get_dashboard_metrics
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Item, Listing, Thread


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def auth_headers():
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    return {"Authorization": f"Bearer {token}"}


def test_dashboard_metrics_counts_live():
    with get_db_session() as session:
        # 1. Awaiting approval item
        it_app = Item(
            code="DA1",
            kind="SELL",
            title="Dashboard Awaiting Approval Item",
            category_key="general",
            attrs={},
            condition_key="used",
            defects={},
            status=ItemStatus.AWAITING_APPROVAL.value,
            target_price=Decimal("1500.00"),
            target_currency="CZK",
        )
        session.add(it_app)

        # 2. Escalated thread
        th_esc = Thread(
            id=str(uuid.uuid4()),
            channel="MARKETPLACE",
            counterparty="buyer_suspicious@example.com",
            lang="cs",
            status="ESCALATED",
        )
        session.add(th_esc)

        # 3. Expiring reservation
        it_res = Item(
            code="DR1",
            kind="SELL",
            title="Dashboard Reserved Item",
            category_key="general",
            attrs={},
            condition_key="used",
            defects={},
            status=ItemStatus.RESERVED.value,
            target_price=Decimal("2500.00"),
            target_currency="CZK",
        )
        session.add(it_res)

        # 4. Stale item (created 10 days ago)
        it_stale = Item(
            code="DS1",
            kind="SELL",
            title="Dashboard Stale Item",
            category_key="general",
            attrs={},
            condition_key="used",
            defects={},
            status=ItemStatus.PUBLISHED.value,
            target_price=Decimal("3500.00"),
            target_currency="CZK",
        )
        it_stale.created_at = datetime.now(UTC) - timedelta(days=10)
        session.add(it_stale)

        # 5. Pending shipment deal (status PAID)
        l1 = Listing(
            id=str(uuid.uuid4()),
            item_code="DA1",
            marketplace_key="bazos_cz",
            price=Decimal("1500.00"),
            currency="CZK",
            price_base=Decimal("1500.00"),
            status="PUBLISHED",
        )
        session.add(l1)
        session.flush()

        d_ship = Deal(
            id=str(uuid.uuid4()),
            listing_id=l1.id,
            agreed_price=Decimal("1500.00"),
            currency="CZK",
            price_base=Decimal("1500.00"),
            delivery_method="POST",
            payment_method="TRANSFER",
            status="PAID",
            closed_at=None,
            closure_evidence={},
        )
        session.add(d_ship)

        # 6. Pending payment deal (status OPEN)
        l2 = Listing(
            id=str(uuid.uuid4()),
            item_code="DR1",
            marketplace_key="sbazar_cz",
            price=Decimal("2500.00"),
            currency="CZK",
            price_base=Decimal("2500.00"),
            status="PUBLISHED",
        )
        session.add(l2)
        session.flush()

        d_pay = Deal(
            id=str(uuid.uuid4()),
            listing_id=l2.id,
            agreed_price=Decimal("2500.00"),
            currency="CZK",
            price_base=Decimal("2500.00"),
            delivery_method="HANDOVER",
            payment_method="CASH",
            status="OPEN",
            closed_at=None,
            closure_evidence={},
        )
        session.add(d_pay)
        session.commit()

        # Compute metrics
        metrics = get_dashboard_metrics(session)

        assert metrics["awaiting_approval"]["count"] >= 1
        assert metrics["escalated_messages"]["count"] >= 1
        assert metrics["expiring_reservations"]["count"] >= 1
        assert metrics["stale_lots"]["count"] >= 1
        assert metrics["pending_shipments"]["count"] >= 1
        assert metrics["pending_payment_confirmations"]["count"] >= 1

        # Check links
        assert metrics["awaiting_approval"]["link"] == "/ui/items?status_filter=AWAITING_APPROVAL"
        assert metrics["escalated_messages"]["link"] == "/ui/inbox?filter=escalated"
        assert metrics["expiring_reservations"]["link"] == "/ui/items?status_filter=RESERVED"
        assert metrics["stale_lots"]["link"] == "/ui/items?filter=stale"
        assert metrics["pending_shipments"]["link"] == "/ui/items?filter=pending_shipments"
        assert metrics["pending_payment_confirmations"]["link"] == "/ui/items?filter=pending_payments"

        # Cleanup
        session.delete(d_ship)
        session.delete(d_pay)
        session.delete(l1)
        session.delete(l2)
        session.delete(it_app)
        session.delete(th_esc)
        session.delete(it_res)
        session.delete(it_stale)
        session.commit()


def test_dashboard_ui_renders_six_clickable_blocks(client, auth_headers):
    resp = client.get("/ui/dashboard", headers=auth_headers)
    assert resp.status_code == 200
    html = resp.text

    # Verify all 6 blocks are present with data-testid
    assert 'data-testid="block-awaiting_approval"' in html
    assert 'data-testid="block-escalated_messages"' in html
    assert 'data-testid="block-expiring_reservations"' in html
    assert 'data-testid="block-stale_lots"' in html
    assert 'data-testid="block-pending_shipments"' in html
    assert 'data-testid="block-pending_payment_confirmations"' in html

    # Verify live count badges exist
    assert 'data-testid="count-awaiting_approval"' in html
    assert 'data-testid="count-escalated_messages"' in html
    assert 'data-testid="count-expiring_reservations"' in html
    assert 'data-testid="count-stale_lots"' in html
    assert 'data-testid="count-pending_shipments"' in html
    assert 'data-testid="count-pending_payment_confirmations"' in html

    # Verify click targets with links to filtered lists
    assert 'href="/ui/items?status_filter=AWAITING_APPROVAL"' in html
    assert 'href="/ui/inbox?filter=escalated"' in html
    assert 'href="/ui/items?status_filter=RESERVED"' in html
    assert 'href="/ui/items?filter=stale"' in html


def test_dashboard_block_navigation_destinations(client, auth_headers):
    # Test navigation to each filtered list destination
    r1 = client.get("/ui/items?status_filter=AWAITING_APPROVAL", headers=auth_headers)
    assert r1.status_code == 200

    r2 = client.get("/ui/inbox?filter=escalated", headers=auth_headers)
    assert r2.status_code == 200

    r3 = client.get("/ui/items?status_filter=RESERVED", headers=auth_headers)
    assert r3.status_code == 200

    r4 = client.get("/ui/items?filter=stale", headers=auth_headers)
    assert r4.status_code == 200
