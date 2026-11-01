"""Tests for Stage E8 (Покупка, сделки, архив)."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from tests.conftest import clean_database

from nextlife.connectors.base import BaseConnector, RawListing
from nextlife.core.config import load_config
from nextlife.core.status import ItemStatus
from nextlife.services.buy import (
    contact_seller,
    create_buy_request,
    execute_buy_search,
)
from nextlife.services.deals import (
    MarketplaceClosureMismatchError,
    archive_item_with_confirmation,
    create_deal,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    BuyCandidate,
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
    with get_db_session() as session:
        clean_database(session)
        yield session
        clean_database(session)


def test_buy_search_and_report_with_stats(clean_db_session: Session) -> None:
    session = clean_db_session
    cfg = load_config()

    req = create_buy_request(session, query="Sony A6000", budget=Decimal("400.00"), currency="EUR")
    assert req.id is not None

    # Mock 5 candidates to meet min_comparables threshold
    mock_items = [
        RawListing(
            "Sony A6000 Black", Decimal("350.00"), "EUR", "https://mock.cz/1", None, None, {}
        ),
        RawListing(
            "Sony A6000 Silver", Decimal("380.00"), "EUR", "https://mock.cz/2", None, None, {}
        ),
        RawListing("Sony A6000 Kit", Decimal("420.00"), "EUR", "https://mock.cz/3", None, None, {}),
        RawListing(
            "Sony A6000 Body", Decimal("310.00"), "EUR", "https://mock.cz/4", None, None, {}
        ),
        RawListing(
            "Sony A6000 Mint", Decimal("390.00"), "EUR", "https://mock.cz/5", None, None, {}
        ),
    ]
    connector = MockSearchConnector("bazos_cz", mock_items)

    report = execute_buy_search(session, req.id, connectors={"bazos_cz": connector}, config=cfg)
    assert report["total_candidates"] == 5
    assert report["price_stats"]["sufficient_data"] is True
    assert Decimal(report["price_stats"]["median"]) == Decimal("380.00")
    assert len(report["candidates"]) == 5
    assert report["candidates"][0]["formatted"] != ""


def test_buy_search_below_min_comparables_omits_range(clean_db_session: Session) -> None:
    session = clean_db_session
    cfg = load_config()

    req = create_buy_request(session, query="Rare Leica", budget=Decimal("1500.00"), currency="EUR")

    # Only 2 candidates (< 5 threshold)
    mock_items = [
        RawListing(
            "Rare Leica M3", Decimal("1400.00"), "EUR", "https://mock.cz/m3", None, None, {}
        ),
        RawListing(
            "Rare Leica M2", Decimal("1300.00"), "EUR", "https://mock.cz/m2", None, None, {}
        ),
    ]
    connector = MockSearchConnector("sbazar_cz", mock_items)

    report = execute_buy_search(session, req.id, connectors={"sbazar_cz": connector}, config=cfg)
    assert report["total_candidates"] == 2
    # Rule П-1.3: range is omitted
    assert report["price_stats"]["sufficient_data"] is False
    assert "Выборка недостаточна" in report["price_stats"]["reason"]


def test_contact_seller_records_truth_log(clean_db_session: Session) -> None:
    session = clean_db_session
    req = create_buy_request(session, query="Lens", budget=Decimal("200.00"))
    cand = BuyCandidate(
        id="cand-1",
        request_id=req.id,
        marketplace_key="bazos_cz",
        external_url="https://mock.cz/lens1",
        price=Decimal("180.00"),
        currency="EUR",
        price_base=Decimal("180.00"),
        verdict="OK",
        contacted=False,
    )
    session.add(cand)
    session.commit()

    res = contact_seller(session, cand.id, ["Каково состояние линз?", "Возможен ли самовывоз?"])
    assert res["status"] == "contacted"

    # Verify truth_log entry
    tlog = (
        session.query(TruthLog)
        .filter(TruthLog.recipient == "COUNTERPARTY")
        .order_by(TruthLog.at.desc())
        .first()
    )
    assert tlog is not None
    assert "Запрос продавцу" in tlog.claim


class _StubUnpublishConnector:
    """ТЗ «Склад» §2.4: коннектор-заглушка с успешным unpublish."""

    def unpublish(self, external_id: str) -> None:
        return None


def test_deal_creation_and_archival_with_confirmation(clean_db_session: Session, monkeypatch) -> None:
    # ТЗ «Склад» §2.1.6/§2.4: снятие через коннекторы реестра — заглушка с успехом.
    from nextlife.connectors import registry

    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: _StubUnpublishConnector())
    session = clean_db_session
    code = "801"
    item = Item(
        code=code,
        kind="SELL",
        title="Test Camera",
        category_key="electronics",
        attrs={},
        condition_key="used",
        defects={},
        status=ItemStatus.PUBLISHED.value,
        target_price=Decimal("250.00"),
        target_currency="EUR",
    )
    session.add(item)
    session.commit()

    listing = Listing(
        id="list-801",
        item_code=code,
        marketplace_key="bazos_cz",
        price=Decimal("250.00"),
        currency="EUR",
        price_base=Decimal("250.00"),
        status="ACTIVE",
        publish_evidence={"mock": True},
    )
    session.add(listing)
    session.commit()

    # 1. Create deal -> transitions item to SOLD
    deal = create_deal(
        session=session,
        listing_id=listing.id,
        agreed_price=Decimal("240.00"),
        currency="EUR",
        price_base=Decimal("240.00"),
        delivery_method="pickup",
        payment_method="cash",
    )
    assert deal.id is not None
    assert item.status == ItemStatus.SOLD.value

    # 2. Marketplace fails to confirm closure -> Archival MUST fail and status must NOT change
    with pytest.raises(MarketplaceClosureMismatchError, match="did not confirm closure"):
        archive_item_with_confirmation(
            session=session,
            item_code=code,
            mock_closure_state="ACTIVE",  # Still active on marketplace!
        )
    assert item.status == ItemStatus.SOLD.value

    # 3. Marketplace confirms closure -> Archival succeeds
    arch_res = archive_item_with_confirmation(
        session=session,
        item_code=code,
        mock_closure_state="CLOSED",
        mock_closure_evidence={"verified": True},
    )
    assert arch_res["status"] == "ARCHIVED"
    assert item.status == ItemStatus.ARCHIVED.value
    assert listing.status == "ARCHIVED"
