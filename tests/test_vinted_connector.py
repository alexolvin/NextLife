"""Vinted web connector skeleton tests (owner decision 2026-10-10).

Verifies: the connector is registered, dry-run publish returns a controlled
result without network or browser, live publish raises explicitly, and the
publish_channel=web marker routes Vinted through the web publish path
(without breaking the ANDROID search channel).
"""

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.connectors.registry import CONNECTOR_MAP, get_connector
from nextlife.connectors.vinted import VintedConnector
from nextlife.core.config import load_config


@pytest.fixture
def vinted_connector():
    config = load_config()
    return VintedConnector(config.marketplaces["vinted"])


def test_vinted_connector_registered():
    """The Vinted connector is in the registry under its config key."""
    assert "vinted" in CONNECTOR_MAP
    assert CONNECTOR_MAP["vinted"] is VintedConnector
    config = load_config()
    connector = get_connector("vinted", config)
    assert isinstance(connector, VintedConnector)


def test_vinted_publish_channel_is_web():
    """The config marker routes Vinted publish through the web channel."""
    config = load_config()
    mp_cfg = config.marketplaces["vinted"]
    assert mp_cfg.publish_channel == "web"
    assert mp_cfg.access_mode == "ANDROID"
    assert mp_cfg.android is not None


def test_vinted_dry_run_publish_returns_controlled_result(vinted_connector):
    """Dry-run publish returns a structured PublishResult without network or browser."""
    lot_data = {
        "title": "Apple iPhone 13 128GB",
        "description": "Telefon ve skvělém stavu.",
        "item_code": "138",
    }
    result = vinted_connector.publish(lot_data, dry_run=True)
    assert result.external_id.startswith("dry_vinted_")
    assert result.external_url.startswith("https://www.vinted.cz/item/")
    assert result.evidence["dry_run"] is True
    assert result.evidence["connector"] == "vinted_web_skeleton"
    # Structured dry-run publish-flow: every step recorded as a no-op, in order.
    steps = result.evidence["steps"]
    assert [s["step"] for s in steps] == [
        "open_sell_form",
        "fill_title",
        "fill_price",
        "fill_description",
        "upload_photos",
        "submit",
        "parse_result",
    ]
    assert all(s["mode"] == "dry_run" for s in steps)
    assert all(s["result"] == "ok" for s in steps)


def test_vinted_live_publish_raises_explicitly(vinted_connector, monkeypatch):
    """Live publish (no dry-run) raises an honest error, not a fake result."""
    monkeypatch.delenv("NEXTLIFE_PUBLISH_DRY_RUN", raising=False)
    lot_data = {
        "title": "Apple iPhone 13 128GB",
        "description": "Telefon ve skvělém stavu.",
        "item_code": "138",
    }
    with pytest.raises(CapabilityNotSupported, match="live web publish not implemented"):
        vinted_connector.publish(lot_data, dry_run=False)


def test_vinted_publish_filters_internal_fields(vinted_connector):
    """The outgoing-field filter runs even in dry-run mode."""
    lot_data = {
        "title": "Apple iPhone 13",
        "description": "Telefon",
        "price_floor": 7000,
    }
    with pytest.raises(InternalFieldLeakageError):
        vinted_connector.publish(lot_data, dry_run=True)


def test_vinted_search_web_catalog(vinted_connector):
    """Web search parses catalog HTML into RawListing items."""
    from decimal import Decimal
    from nextlife.connectors.vinted import parse_vinted_catalog
    sample_html = '''
    <div data-testid="product-item-id-998877">
      <img src="https://images1.vinted.net/photo.webp" alt="Apple iPhone 12 Pro 128GB, Značka: Apple, Stav: Velmi dobrý, 7500.00 Kč, 7800.00 Kč"/>
      <a href="/items/998877-apple-iphone-12-pro?referrer=catalog">Link</a>
    </div>
    '''
    items = parse_vinted_catalog(sample_html, base_url="https://www.vinted.cz")
    assert len(items) == 1
    assert items[0].title == "Apple iPhone 12 Pro 128GB"
    assert items[0].price == Decimal("7500.00")
    assert items[0].currency == "CZK"
    assert items[0].external_url == "https://www.vinted.cz/items/998877-apple-iphone-12-pro"
    assert items[0].condition_text == "Velmi dobrý"
    assert items[0].raw["brand"] == "Apple"
    assert items[0].raw["image_url"] == "https://images1.vinted.net/photo.webp"


def test_vinted_not_excluded_as_android_channel_in_build_draft():
    """Vinted (publish_channel=web) is not excluded as 'android_channel' in build_draft.

    Before the sell.py change, Vinted (access_mode=ANDROID) was excluded as
    'android_channel' for publish. After the change the web publish path is open.
    The row may still be 'marketplace_disabled' if the registry row is disabled
    (enabled=False for ANDROID) — a separate operational concern, not the
    android_channel gate this test verifies.
    """
    from decimal import Decimal

    from sqlalchemy import select

    from nextlife.services.lot_creation import create_item_and_lot
    from nextlife.services.sell import build_draft
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item, Lot, Marketplace

    cfg = load_config()
    with get_db_session() as session:
        # Ensure the vinted registry row exists (real state: ANDROID -> enabled=False).
        mp = session.scalar(select(Marketplace).where(Marketplace.key == "vinted"))
        if not mp:
            session.add(
                Marketplace(
                    key="vinted",
                    title="Vinted",
                    country="CZ",
                    currency="CZK",
                    access_mode="ANDROID",
                    capabilities={},
                    config_hash="test",
                )
            )
            session.flush()

        created = create_item_and_lot(
            session=session,
            title="Test Vinted Eligibility",
            category_key="electronics",
            price=Decimal("1000.00"),
            currency="CZK",
            price_floor=Decimal("800.00"),
            shipping_policy="BUYER_PAYS",
        )
        item_code = created["item_code"]
        lot_code = created["lot_code"]

        draft = build_draft(session, item_code, cfg)
        excluded = draft["excluded_marketplaces"]
        assert excluded.get("vinted") != "android_channel", (
            f"Vinted must not be excluded as 'android_channel', got: {excluded.get('vinted')}"
        )

        # Cleanup test entities.
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_vinted_service_layer_dry_run_reaches_connector():
    """Full service-layer dry-run: Vinted is eligible in build_draft and
    publish_item reaches the Vinted connector, returning a controlled result
    with the structured steps (no phone, no browser)."""
    from decimal import Decimal

    from sqlalchemy import select

    from nextlife.core.approval import record_approval
    from nextlife.core.status import ItemStatus
    from nextlife.services.lot_creation import create_item_and_lot
    from nextlife.services.sell import build_draft, publish_item
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import (
        Approval,
        Item,
        Job,
        Listing,
        Lot,
        Marketplace,
    )

    cfg = load_config()
    with get_db_session() as session:
        # Enable the vinted registry row so build_draft includes it (the ANDROID
        # row is disabled by sync_marketplaces; enabling is the operational step
        # for the web publish path).
        mp = session.scalar(select(Marketplace).where(Marketplace.key == "vinted"))
        if not mp:
            mp = Marketplace(
                key="vinted",
                title="Vinted",
                country="CZ",
                currency="CZK",
                access_mode="ANDROID",
                capabilities={},
                config_hash="test",
            )
            session.add(mp)
            session.flush()
        mp.enabled = True
        session.flush()

        created = create_item_and_lot(
            session=session,
            title="Test Vinted Service Layer",
            category_key="electronics",
            price=Decimal("1000.00"),
            currency="CZK",
            price_floor=Decimal("800.00"),
            shipping_policy="BUYER_PAYS",
        )
        item_code = created["item_code"]
        lot_code = created["lot_code"]

        draft = build_draft(session, item_code, cfg)
        assert "vinted" in draft["eligible_marketplaces"], (
            f"Vinted should be eligible, got: {draft['eligible_marketplaces']}, "
            f"excluded: {draft['excluded_marketplaces']}"
        )
        assert draft["excluded_marketplaces"].get("vinted") != "android_channel"

        # Approval (required by publish_item).
        payload_hash = draft["payload_hash"]
        clean_payload = {k: v for k, v in draft.items() if k != "payload_hash"}
        record_approval(session, item_code, clean_payload, payload_hash, channel="TELEGRAM")
        item = session.get(Item, item_code)
        item.status = ItemStatus.APPROVED.value
        session.flush()

        # Dry-run publish with the Vinted connector: must reach the connector
        # and return the structured steps (no phone, no browser).
        vinted = VintedConnector(cfg.marketplaces["vinted"])
        res = publish_item(
            session, item_code, draft, connectors={"vinted": vinted}, config=cfg, dry_run=True
        )
        assert "vinted" in res["successes"], f"Vinted should succeed, got: {res}"

        listing = session.scalar(
            select(Listing).where(
                Listing.item_code == item_code, Listing.marketplace_key == "vinted"
            )
        )
        assert listing is not None
        assert listing.publish_evidence.get("dry_run") is True
        steps = listing.publish_evidence.get("steps")
        assert steps is not None
        assert [s["step"] for s in steps] == [
            "open_sell_form",
            "fill_title",
            "fill_price",
            "fill_description",
            "upload_photos",
            "submit",
            "parse_result",
        ]

        # Cleanup (restore the vinted row to disabled, matching the real state).
        mp.enabled = False
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Job).filter(Job.idempotency_key.like(f"{item_code}_%")).delete()
        session.query(Approval).filter(Approval.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


class _FakeBrowserContext:
    """Records the context method calls in order (no Playwright)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def goto_sell_form(self) -> None:
        self.calls.append("open_sell_form")

    def fill_title(self, title: str) -> None:
        self.calls.append("fill_title")

    def fill_price(self, price: str) -> None:
        self.calls.append("fill_price")

    def fill_description(self, description: str) -> None:
        self.calls.append("fill_description")

    def upload_photos(self, photos: list[str]) -> None:
        self.calls.append("upload_photos")

    def submit(self) -> None:
        self.calls.append("submit")

    def parse_result(self) -> dict:
        self.calls.append("parse_result")
        return {}


def test_vinted_publish_flow_calls_context_in_order():
    """The publish flow calls the context steps in the right order (mock)."""
    from nextlife.connectors.vinted import VintedPublishFlow

    ctx = _FakeBrowserContext()
    flow = VintedPublishFlow(context=ctx)
    steps = flow.run({"title": "Test", "price": "1000", "description": "desc"})
    assert ctx.calls == [
        "open_sell_form",
        "fill_title",
        "fill_price",
        "fill_description",
        "upload_photos",
        "submit",
        "parse_result",
    ]
    assert [s["step"] for s in steps] == [
        "open_sell_form",
        "fill_title",
        "fill_price",
        "fill_description",
        "upload_photos",
        "submit",
        "parse_result",
    ]
    # A context means live mode (real actions would run here).
    assert all(s["mode"] == "live" for s in steps)


def test_vinted_publish_flow_dry_run_no_context_noop():
    """In dry-run (no context) the steps are no-op stubs, no external calls."""
    from nextlife.connectors.vinted import VintedPublishFlow

    flow = VintedPublishFlow(context=None)
    steps = flow.run({"title": "Test", "price": "1000", "description": "desc"})
    assert len(steps) == 7
    assert all(s["mode"] == "dry_run" for s in steps)
    assert all(s["result"] == "ok" for s in steps)
