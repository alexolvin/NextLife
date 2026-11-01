"""Tests for Stage E9 (Сквозная приёмка: полный цикл продажи и покупки в --dry-run, сверка truth_log)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.conftest import clean_database

from nextlife.connectors.base import BaseConnector, RawListing
from nextlife.connectors.registry import sync_marketplaces
from nextlife.core.approval import record_approval
from nextlife.core.codes import allocate_code
from nextlife.core.config import load_config
from nextlife.core.status import ItemStatus
from nextlife.services.buy import (
    contact_seller,
    create_buy_request,
    execute_buy_search,
)
from nextlife.services.deals import (
    archive_item_with_confirmation,
    create_deal,
)
from nextlife.services.research import build_research_report, run_research
from nextlife.services.sell import build_draft, publish_item
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    AuditEvent,
    FxRate,
    Item,
    Listing,
    TruthLog,
)


class MockSearchConnector(BaseConnector):
    def __init__(self, key: str, listings: list[RawListing]) -> None:
        self.key = key
        self.listings = listings

    def search(self, query: str) -> list[RawListing]:
        return self.listings


@pytest.fixture
def clean_db_session() -> Session:
    config = load_config()
    with get_db_session() as session:
        sync_marketplaces(session, config)
        clean_database(session)
        yield session
        clean_database(session)


def test_end_to_end_sell_cycle_in_dry_run(clean_db_session: Session) -> None:
    """Full cycle of selling an item from creation, research, approval, dry-run publication, deal, and archive."""
    session = clean_db_session
    cfg = load_config()

    # Step 1: Allocate unique 3-digit code
    code = allocate_code(session, release_days=cfg.app.code_release_days)
    assert len(code) == 3

    # Seed FX Rate
    today = date.today()
    session.merge(
        FxRate(
            rate_date=today,
            currency="CZK",
            rate_to_base=Decimal("25.0"),
            source="ECB",
            fetched_at=datetime.now(UTC),
        )
    )

    # Step 2: Create Item in DRAFT
    item = Item(
        code=code,
        kind="SELL",
        title="Fujifilm X-T30",
        category_key="electronics",
        attrs={"pickup_allowed": True},
        condition_key="used",
        defects={},
        status=ItemStatus.DRAFT.value,
        target_price=Decimal("600.00"),
        target_currency="EUR",
    )
    session.add(item)
    session.commit()

    # Step 3: Market research with simulated comparables
    raw_comparables = [
        (
            "bazos_cz",
            RawListing("Fuji X-T30", Decimal("14500"), "CZK", "https://mock/1", None, None, {}),
        ),
        (
            "bazos_cz",
            RawListing(
                "Fuji X-T30 Black", Decimal("15000"), "CZK", "https://mock/2", None, None, {}
            ),
        ),
        (
            "sbazar_cz",
            RawListing("Fujifilm XT30", Decimal("15500"), "CZK", "https://mock/3", None, None, {}),
        ),
        (
            "sbazar_cz",
            RawListing(
                "Fuji XT-30 body", Decimal("14000"), "CZK", "https://mock/4", None, None, {}
            ),
        ),
        (
            "aukro_cz",
            RawListing("Fuji XT30 mint", Decimal("16000"), "CZK", "https://mock/5", None, None, {}),
        ),
    ]
    run_id = run_research(
        session, code, "Fujifilm X-T30", cfg, raw_listings_override=raw_comparables
    )
    report = build_research_report(session, run_id, cfg)
    assert report["total_found"] == 5
    assert report["insufficient_sample"] is False
    assert report["price_range"] is not None

    item.status = ItemStatus.RESEARCHED.value
    session.commit()

    # Step 4: Build draft and approve
    draft = build_draft(session, code, cfg)
    payload_hash = draft["payload_hash"]
    clean_payload = {k: v for k, v in draft.items() if k != "payload_hash"}
    record_approval(session, code, clean_payload, payload_hash, channel="TELEGRAM")
    item.status = ItemStatus.APPROVED.value
    session.commit()

    # Step 5: Publish in dry-run mode
    pub_res = publish_item(session, code, draft, config=cfg, dry_run=True)
    assert pub_res["status"] == ItemStatus.PUBLISHED.value
    assert len(pub_res["successes"]) > 0

    listings = list(
        session.execute(select(Listing).where(Listing.item_code == code)).scalars().all()
    )
    assert len(listings) > 0
    for lst in listings:
        assert lst.status == "ACTIVE"
        assert lst.external_url is not None
        assert lst.publish_evidence.get("dry_run") is True

    # Step 6: Deal closure
    deal = create_deal(
        session=session,
        listing_id=listings[0].id,
        agreed_price=Decimal("580.00"),
        currency="EUR",
        price_base=Decimal("580.00"),
        delivery_method="pickup",
        payment_method="cash",
    )
    assert deal.id is not None
    assert item.status == ItemStatus.SOLD.value

    # Step 7: Archival with confirmed closure evidence
    arch_res = archive_item_with_confirmation(
        session=session,
        item_code=code,
        mock_closure_state="CLOSED",
        mock_closure_evidence={"verified_closure": True},
    )
    assert arch_res["status"] == "ARCHIVED"
    assert item.status == ItemStatus.ARCHIVED.value

    # Step 8: Verify truth_log consistency across whole cycle
    claims = list(session.execute(select(TruthLog).order_by(TruthLog.at.asc())).scalars().all())
    assert len(claims) >= 2
    for claim in claims:
        # Every claim must have non-empty evidence
        assert claim.evidence is not None
        assert len(claim.evidence) > 0

    # Step 9: Verify audit events were created
    events = list(
        session.execute(select(AuditEvent).where(AuditEvent.entity_id == code)).scalars().all()
    )
    assert len(events) >= 1


def test_end_to_end_buy_cycle(clean_db_session: Session) -> None:
    """Full cycle of buying: request, search, currency conversion, candidate evaluation, and seller contact."""
    session = clean_db_session
    cfg = load_config()

    # Step 1: Create buy request
    req = create_buy_request(
        session, query="MacBook Air M1", budget=Decimal("650.00"), currency="EUR"
    )
    assert req.id is not None

    # Step 2: Run search with comparables
    mock_listings = [
        RawListing(
            "MacBook Air M1 8GB 256GB",
            Decimal("600.00"),
            "EUR",
            "https://mock.cz/mac1",
            None,
            None,
            {},
        ),
        RawListing(
            "Apple MacBook Air M1", Decimal("620.00"), "EUR", "https://mock.cz/mac2", None, None, {}
        ),
        RawListing(
            "MacBook Air M1 Silver",
            Decimal("640.00"),
            "EUR",
            "https://mock.cz/mac3",
            None,
            None,
            {},
        ),
        RawListing(
            "MacBook Air M1 Space Gray",
            Decimal("590.00"),
            "EUR",
            "https://mock.cz/mac4",
            None,
            None,
            {},
        ),
        RawListing(
            "MacBook Air M1 Gold", Decimal("630.00"), "EUR", "https://mock.cz/mac5", None, None, {}
        ),
    ]
    connector = MockSearchConnector("bazos_cz", mock_listings)

    report = execute_buy_search(session, req.id, connectors={"bazos_cz": connector}, config=cfg)
    assert report["total_candidates"] == 5
    assert report["price_stats"]["sufficient_data"] is True
    assert report["candidates"][0]["verdict"] == "Ниже бюджета"

    # Step 3: Contact seller for candidate
    cand_id = report["candidates"][0]["id"]
    contact_res = contact_seller(session, cand_id, ["Каково состояние аккумулятора?"])
    assert contact_res["status"] == "contacted"

    # Step 4: Verify truth log
    tlog = (
        session.query(TruthLog)
        .filter(TruthLog.recipient == "COUNTERPARTY")
        .order_by(TruthLog.at.desc())
        .first()
    )
    assert tlog is not None
    assert "Запрос продавцу" in tlog.claim
