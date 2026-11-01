"""Tests for Saved Queries Polling & Deduplication (Task T-070 / TZ v2.0 Section I.3).

DoD:
- Continuous search based on query criteria with poll interval.
- Deduplication: repeat discovery of identical listing produces zero repeat notifications.
- Outgoing filter verifies zero leakage of sensitive internal fields.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from nextlife.services.saved_queries import (
    create_saved_buy_request,
    poll_saved_queries,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BuyCandidate, BuyRequest, Marketplace, Outbox


@pytest.fixture
def ensure_marketplace():
    with get_db_session() as session:
        mp = session.get(Marketplace, "bazos_cz")
        if not mp:
            mp = Marketplace(
                key="bazos_cz",
                name="Bazoš.cz",
                base_url="https://bazos.cz",
                adapter_kind="HTML",
                is_active=True,
            )
            session.add(mp)
            session.commit()
    yield "bazos_cz"


def test_saved_query_polling_and_deduplication(ensure_marketplace):
    with get_db_session() as session:
        # Create a saved buy request
        req = create_saved_buy_request(
            session=session,
            query="ThinkPad T14",
            budget=Decimal("15000.00"),
            currency="CZK",
            poll_interval_seconds=60,
        )
        session.commit()

        # Batch 1 results: 2 listings
        mock_batch_1 = [
            {
                "external_id": "bazos-111",
                "external_url": "https://bazos.cz/inzerat/111/thinkpad-t14.php",
                "title": "Lenovo ThinkPad T14 Gen 2",
                "price": Decimal("14000.00"),
                "currency": "CZK",
            },
            {
                "external_id": "bazos-222",
                "external_url": "https://bazos.cz/inzerat/222/thinkpad-t14.php",
                "title": "Lenovo ThinkPad T14 AMD",
                "price": Decimal("13500.00"),
                "currency": "CZK",
            },
        ]

        def search_mock_1(query, attrs):
            return mock_batch_1

        try:
            # First poll
            res1 = poll_saved_queries(session, search_mock_1, marketplace_key="bazos_cz")
            session.commit()

            assert res1["due_requests_count"] == 1
            assert res1["total_discovered"] == 2
            assert res1["new_candidates"] == 2
            assert res1["notifications_sent"] == 2

            # Verify candidates in DB
            candidates = session.query(BuyCandidate).filter(BuyCandidate.request_id == req.id).all()
            assert len(candidates) == 2
            urls = [c.external_url for c in candidates]
            assert "https://bazos.cz/inzerat/111/thinkpad-t14.php" in urls
            assert "https://bazos.cz/inzerat/222/thinkpad-t14.php" in urls

            # Fast forward time or reset last_polled_at to simulate next interval
            req_db = session.get(BuyRequest, req.id)
            req_db.last_polled_at = datetime.now(UTC) - timedelta(seconds=120)
            session.commit()

            # Batch 2 results: same 2 listings + 1 new listing
            mock_batch_2 = list(mock_batch_1) + [
                {
                    "external_id": "bazos-333",
                    "external_url": "https://bazos.cz/inzerat/333/thinkpad-t14-gen3.php",
                    "title": "Lenovo ThinkPad T14 Gen 3",
                    "price": Decimal("15000.00"),
                    "currency": "CZK",
                }
            ]

            def search_mock_2(query, attrs):
                return mock_batch_2

            # Second poll: must deduplicate the 2 seen listings!
            res2 = poll_saved_queries(session, search_mock_2, marketplace_key="bazos_cz")
            session.commit()

            assert res2["due_requests_count"] == 1
            assert res2["total_discovered"] == 3
            # DoD check: only the 1 NEW candidate is added, no duplicates!
            assert res2["new_candidates"] == 1
            assert res2["notifications_sent"] == 1

            # Total candidates now must be exactly 3
            candidates_after = session.query(BuyCandidate).filter(BuyCandidate.request_id == req.id).all()
            assert len(candidates_after) == 3
        finally:
            # Cleanup
            session.query(Outbox).filter(Outbox.recipient_hint == "OWNER").delete()
            session.query(BuyCandidate).filter(BuyCandidate.request_id == req.id).delete()
            session.query(BuyRequest).filter(BuyRequest.id == req.id).delete()
            session.commit()


def test_poll_interval_skips_not_due_requests(ensure_marketplace):
    with get_db_session() as session:
        req = create_saved_buy_request(
            session=session,
            query="MacBook Air M1",
            budget=Decimal("18000.00"),
            currency="CZK",
            poll_interval_seconds=300,
        )
        req.last_polled_at = datetime.now(UTC)  # just polled
        session.commit()

        def search_dummy(query, attrs):
            return [{"external_url": "https://bazos.cz/mac", "price": Decimal("17000.00")}]

        try:
            res = poll_saved_queries(session, search_dummy, marketplace_key="bazos_cz")
            assert res["due_requests_count"] == 0
            assert res["new_candidates"] == 0
        finally:
            session.query(BuyRequest).filter(BuyRequest.id == req.id).delete()
            session.commit()
