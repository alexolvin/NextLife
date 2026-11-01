"""Tests for Candidate Evaluation (TZ v1.1 section 10 / Task T-071).

DoD:
- Price normalization.
- Deviation from median.
- Criteria matching and distance estimation.
- DoD: when sample size < min_comparables, price range is NOT displayed.
"""

import uuid
from decimal import Decimal

from nextlife.services.candidate_evaluation import (
    calculate_criteria_match,
    estimate_distance_km,
    evaluate_candidates,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BuyRequest


def test_distance_and_criteria_match_helpers():
    # Distance
    assert estimate_distance_km("Praha 1") == 0
    assert estimate_distance_km("Plzeň") == 90
    assert estimate_distance_km("Brno - střed") == 205
    assert estimate_distance_km("Unknown City") is None

    # Criteria
    req_attrs = {"brand": "Apple", "min_ram_gb": 16}
    cand_match = {"brand": "Apple MacBook", "min_ram_gb": 16}
    score, passed, missing = calculate_criteria_match(req_attrs, cand_match)
    assert score == 1.0
    assert len(missing) == 0

    cand_fail = {"brand": "Dell", "min_ram_gb": 8}
    score2, passed2, missing2 = calculate_criteria_match(req_attrs, cand_fail)
    assert score2 == 0.0
    assert len(missing2) == 2


def test_sample_less_than_min_comparables_suppresses_price_range():
    with get_db_session() as session:
        req = BuyRequest(
            id=str(uuid.uuid4()),
            query="Rare Vintage Lens",
            budget=Decimal("12000.00"),
            currency="EUR",
            attrs={"condition": "good"},
            status="ACTIVE",
        )
        session.add(req)
        session.commit()

        # Only 3 candidates (min_comparables is 5)
        candidates = [
            {"price": Decimal("9000.00"), "currency": "EUR", "external_url": "http://1", "attrs": {"condition": "good"}},
            {"price": Decimal("9500.00"), "currency": "EUR", "external_url": "http://2", "attrs": {"condition": "good"}},
            {"price": Decimal("10000.00"), "currency": "EUR", "external_url": "http://3", "attrs": {"condition": "good"}},
        ]

        try:
            res = evaluate_candidates(session, req.id, candidates)

            # DoD: when sample < min_comparables, price range MUST NOT be displayed!
            assert res["sample_size"] == 3
            assert res["min_comparables"] == 5
            assert res["sufficient_data"] is False
            assert res["price_range"] is None, "DoD violation: price range must be suppressed when sample < min_comparables"

            for c in res["candidates"]:
                assert c["deviation_from_median_pct"] is None
                assert c["verdict"] == "WITHIN_BUDGET"
        finally:
            session.query(BuyRequest).filter(BuyRequest.id == req.id).delete()
            session.commit()


def test_sample_equal_or_greater_min_comparables_computes_range_and_deviation():
    with get_db_session() as session:
        req = BuyRequest(
            id=str(uuid.uuid4()),
            query="RTX 4070",
            budget=Decimal("17000.00"),
            currency="EUR",
            attrs={"brand": "Nvidia"},
            status="ACTIVE",
        )
        session.add(req)
        session.commit()

        # 7 candidates with consistent distribution around 14000 EUR
        candidates = [
            {"price": Decimal("11500.00"), "currency": "EUR", "external_url": "http://1", "location": "Praha", "attrs": {"brand": "Nvidia"}}, # Bargain (< -15%)
            {"price": Decimal("13500.00"), "currency": "EUR", "external_url": "http://2", "location": "Plzeň", "attrs": {"brand": "Nvidia"}},
            {"price": Decimal("13800.00"), "currency": "EUR", "external_url": "http://3", "location": "Brno", "attrs": {"brand": "Nvidia"}},
            {"price": Decimal("14000.00"), "currency": "EUR", "external_url": "http://4", "location": "Praha", "attrs": {"brand": "Nvidia"}},
            {"price": Decimal("14200.00"), "currency": "EUR", "external_url": "http://5", "location": "Praha", "attrs": {"brand": "Nvidia"}},
            {"price": Decimal("14500.00"), "currency": "EUR", "external_url": "http://6", "location": "Praha", "attrs": {"brand": "Nvidia"}},
            {"price": Decimal("16500.00"), "currency": "EUR", "external_url": "http://7", "location": "Ostrava", "attrs": {"brand": "Nvidia"}}, # Overpriced (> 15%)
        ]

        try:
            res = evaluate_candidates(session, req.id, candidates)

            assert res["sample_size"] == 7
            assert res["sufficient_data"] is True
            assert res["price_range"] is not None
            assert "min" in res["price_range"]
            assert "median" in res["price_range"]
            assert "max" in res["price_range"]

            cands = res["candidates"]
            bargain = next(c for c in cands if c["external_url"] == "http://1")
            assert bargain["deviation_from_median_pct"] is not None
            assert bargain["deviation_from_median_pct"] <= -15.0
            assert bargain["verdict"] == "BARGAIN"
            assert bargain["distance_km"] == 0

            overpriced = next(c for c in cands if c["external_url"] == "http://7")
            assert overpriced["deviation_from_median_pct"] is not None
            assert overpriced["deviation_from_median_pct"] >= 15.0
            assert overpriced["verdict"] in ("OVERPRICED", "EXCEEDS_BUDGET")
            assert overpriced["distance_km"] == 370
        finally:
            session.query(BuyRequest).filter(BuyRequest.id == req.id).delete()
            session.commit()
