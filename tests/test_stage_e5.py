"""Tests for Stage E5 (Публикация).

The Android-bridge test of this file (test_android_device_offline_blocks_
and_reports_evidence) was removed with the OpenClaw-agent stub (TZ M1 B.1):
the real bridge semantics (mutex, evidence, unknown screen -> BLOCKED) are
covered by tests/test_android_bridge.py.
"""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session
from tests.conftest import clean_database

from nextlife.connectors.base import BaseConnector, PublishResult
from nextlife.connectors.registry import sync_marketplaces
from nextlife.core.approval import compute_payload_hash, record_approval
from nextlife.core.config import load_config
from nextlife.core.status import ItemStatus
from nextlife.services.sell import (
    MissingApprovalError,
    build_draft,
    publish_item,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing


def create_test_item_for_publishing(session: Session, code: str) -> Item:
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
    config = load_config()
    with get_db_session() as session:
        sync_marketplaces(session, config)
        clean_database(session)
        yield session
        clean_database(session)


class MockConnector(BaseConnector):
    def __init__(self, key: str, success: bool = True, return_empty_url: bool = False) -> None:
        self.key = key
        self.success = success
        self.return_empty_url = return_empty_url
        self.published_calls = 0

    def search(self, query: str) -> list[Any]:
        return []

    def publish(self, item_code: str, payload: dict[str, Any]) -> PublishResult:
        self.published_calls += 1
        if not self.success:
            raise RuntimeError(f"Network failure publishing to {self.key}")
        url = "" if self.return_empty_url else f"https://marketplace.mock/{self.key}/{item_code}"
        return PublishResult(
            external_id=f"ext_{item_code}",
            external_url=url,
            evidence={"status": 200, "mock": True},
        )


def test_cannot_publish_without_approval(clean_db_session: Session) -> None:
    session = clean_db_session
    code = "101"
    create_test_item_for_publishing(session, code)

    draft = build_draft(session, code)
    with pytest.raises(MissingApprovalError, match="has not been approved or was modified"):
        publish_item(session, code, draft, dry_run=True)


def test_payload_modification_after_approval_invalidates_approval(
    clean_db_session: Session,
) -> None:
    session = clean_db_session
    code = "102"
    create_test_item_for_publishing(session, code)

    draft = build_draft(session, code)
    payload_hash = draft["payload_hash"]

    # Record approval for original payload
    clean_payload = {k: v for k, v in draft.items() if k != "payload_hash"}
    record_approval(session, code, clean_payload, payload_hash, channel="TELEGRAM")
    session.commit()

    # Modify draft payload (e.g., price tampered)
    tampered_draft = dict(draft)
    tampered_draft["price"] = "999.00"

    with pytest.raises(MissingApprovalError, match="has not been approved or was modified"):
        publish_item(session, code, tampered_draft, dry_run=True)


def test_idempotent_publishing_does_not_create_duplicate_listings(
    clean_db_session: Session,
) -> None:
    session = clean_db_session
    code = "103"
    item = create_test_item_for_publishing(session, code)
    item.status = ItemStatus.APPROVED.value
    session.commit()

    draft = build_draft(session, code)
    payload_hash = draft["payload_hash"]
    clean_payload = {k: v for k, v in draft.items() if k != "payload_hash"}
    record_approval(session, code, clean_payload, payload_hash, channel="TELEGRAM")
    session.commit()

    # First publication run
    res1 = publish_item(session, code, draft, dry_run=True)
    assert res1["status"] == ItemStatus.PUBLISHED.value
    listings_count_1 = session.query(Listing).filter(Listing.item_code == code).count()
    assert listings_count_1 > 0

    # Second run with same payload
    res2 = publish_item(session, code, draft, dry_run=True)
    listings_count_2 = session.query(Listing).filter(Listing.item_code == code).count()
    assert listings_count_2 == listings_count_1
    assert res2["status"] == ItemStatus.PUBLISHED.value


def test_publish_dry_run_env_kill_switch_forces_dry_run(
    clean_db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEXTLIFE_PUBLISH_DRY_RUN=1 forces dry_run even when the caller did not ask for it."""
    session = clean_db_session
    code = "105"
    item = create_test_item_for_publishing(session, code)
    item.status = ItemStatus.APPROVED.value
    session.commit()

    draft = build_draft(session, code)
    payload_hash = draft["payload_hash"]
    clean_payload = {k: v for k, v in draft.items() if k != "payload_hash"}
    record_approval(session, code, clean_payload, payload_hash, channel="TELEGRAM")
    session.commit()

    monkeypatch.setenv("NEXTLIFE_PUBLISH_DRY_RUN", "1")
    res = publish_item(session, code, draft)
    assert res["status"] == ItemStatus.PUBLISHED.value
    listings = session.query(Listing).filter(Listing.item_code == code).all()
    assert listings, "publish must create listings"
    for listing in listings:
        assert listing.external_id.startswith("dry_")
        assert listing.publish_evidence.get("dry_run") is True


def test_publishing_without_external_url_does_not_yield_published(
    clean_db_session: Session,
) -> None:
    session = clean_db_session
    code = "104"
    item = create_test_item_for_publishing(session, code)
    item.status = ItemStatus.APPROVED.value
    session.commit()

    draft = build_draft(session, code)
    payload_hash = draft["payload_hash"]
    clean_payload = {k: v for k, v in draft.items() if k != "payload_hash"}
    record_approval(session, code, clean_payload, payload_hash, channel="TELEGRAM")
    session.commit()

    # Connector returns empty external_url
    bad_connector = MockConnector("bazos_cz", success=True, return_empty_url=True)
    cfg = load_config()
    # Force single marketplace in draft
    draft_single = dict(draft)
    draft_single["eligible_marketplaces"] = ["bazos_cz"]
    single_payload = {k: v for k, v in draft_single.items() if k != "payload_hash"}
    single_hash = compute_payload_hash(single_payload)
    draft_single["payload_hash"] = single_hash
    record_approval(session, code, single_payload, single_hash, channel="TELEGRAM")
    session.commit()

    res = publish_item(
        session,
        code,
        draft_single,
        connectors={"bazos_cz": bad_connector},
        config=cfg,
        dry_run=False,
    )
    assert res["status"] != ItemStatus.PUBLISHED.value
    assert "bazos_cz" in res["failures"]


def test_partial_failure_yields_partially_published(clean_db_session: Session) -> None:
    session = clean_db_session
    code = "105"
    item = create_test_item_for_publishing(session, code)
    item.status = ItemStatus.APPROVED.value
    session.commit()

    draft = build_draft(session, code)
    draft_multi = dict(draft)
    draft_multi["eligible_marketplaces"] = ["bazos_cz", "sbazar_cz"]
    multi_payload = {k: v for k, v in draft_multi.items() if k != "payload_hash"}
    multi_hash = compute_payload_hash(multi_payload)
    draft_multi["payload_hash"] = multi_hash
    record_approval(session, code, multi_payload, multi_hash, channel="TELEGRAM")
    session.commit()

    connectors = {
        "bazos_cz": MockConnector("bazos_cz", success=True),
        "sbazar_cz": MockConnector("sbazar_cz", success=False),
    }

    res = publish_item(
        session,
        code,
        draft_multi,
        connectors=connectors,
        dry_run=False,
    )
    assert res["status"] == ItemStatus.PARTIALLY_PUBLISHED.value
    assert "bazos_cz" in res["successes"]
    assert "sbazar_cz" in res["failures"]
