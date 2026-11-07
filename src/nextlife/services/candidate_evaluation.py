"""Candidate evaluation service (TZ v1.1 section 10 / Task T-071).

DoD:
- Price normalization to base currency.
- Deviation from median calculated when sample >= min_comparables.
- DoD: when sample size < min_comparables, price range is NOT displayed.
- Criteria matching (attributes match score).
- Distance estimation from buyer location.
- Module length <= 400 lines.
"""

import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.money.money import convert_price
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.services.research import calculate_price_stats
from nextlife.storage.models import BuyRequest

CZECH_CITY_DISTANCES_FROM_PRAGUE = {
    "praha": 0,
    "prague": 0,
    "kladno": 30,
    "kolin": 60,
    "plzen": 90,
    "pilsen": 90,
    "usti nad labem": 90,
    "liberec": 110,
    "hradec kralove": 115,
    "pardubice": 125,
    "ceske budejovice": 150,
    "jihlava": 130,
    "brno": 205,
    "olomouc": 280,
    "zlin": 300,
    "ostrava": 370,
}


class CandidateEvaluationError(NextLifeError):
    """Raised when candidate evaluation fails."""


def remove_accents(input_str: str) -> str:
    nfkd = unicodedata.normalize("NFKD", input_str)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])


def estimate_distance_km(listing_city: str | None, buyer_city: str = "Praha") -> int | None:
    """Estimate road distance in km between listing city and buyer city."""
    if not listing_city:
        return None
    city_norm = remove_accents(listing_city).strip().lower()
    for known_city, dist in CZECH_CITY_DISTANCES_FROM_PRAGUE.items():
        if known_city in city_norm:
            return dist
    return None


def calculate_criteria_match(
    requested_attrs: dict[str, Any],
    candidate_attrs: dict[str, Any],
) -> tuple[float, list[str], list[str]]:
    """Compute criteria match score (0.0 - 1.0) and lists of passed/missing attributes."""
    if not requested_attrs:
        return 1.0, [], []

    passed = []
    missing = []

    for req_key, req_val in requested_attrs.items():
        cand_val = candidate_attrs.get(req_key)
        if cand_val is None:
            missing.append(req_key)
        else:
            # Handle numeric min requirements
            if isinstance(req_val, (int, float)) and isinstance(cand_val, (int, float)):
                if req_key.startswith("min_"):
                    if cand_val >= req_val:
                        passed.append(req_key)
                    else:
                        missing.append(f"{req_key} (got {cand_val} < {req_val})")
                else:
                    if cand_val == req_val:
                        passed.append(req_key)
                    else:
                        missing.append(req_key)
            elif str(req_val).strip().lower() in str(cand_val).strip().lower():
                passed.append(req_key)
            else:
                missing.append(req_key)

    total = len(passed) + len(missing)
    score = round(len(passed) / total, 2) if total > 0 else 1.0
    return score, passed, missing


def evaluate_candidates(
    session: Session,
    request_id: str,
    candidate_items: list[dict[str, Any]],
    buyer_location: str = "Praha",
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Evaluate a batch of candidate listings against a BuyRequest.

    DoD:
    - If sample size < min_comparables, price range and median deviation are suppressed.
    """
    config = load_config()
    min_comp = config.app.min_comparables
    base_curr = config.app.base_currency

    buy_req = session.get(BuyRequest, request_id)
    if not buy_req:
        raise CandidateEvaluationError(f"BuyRequest '{request_id}' not found")

    today = datetime.now(UTC).date()
    normalized_candidates = []
    prices_base = []

    # 1. Normalize prices
    for item in candidate_items:
        raw_price = Decimal(str(item.get("price", "0.00")))
        raw_curr = item.get("currency", buy_req.currency)
        conv = convert_price(session, raw_price, raw_curr, today, base_curr)
        price_base = conv.amount_base
        prices_base.append(price_base)

        normalized_candidates.append({
            "raw": item,
            "price_base": price_base,
            "currency": raw_curr,
            "price": raw_price,
            "url": item.get("external_url", ""),
            "external_id": item.get("external_id"),
            "attrs": item.get("attrs", {}),
            "location": item.get("location"),
        })

    # 2. Compute price stats (with min_comparables constraint)
    stats = calculate_price_stats(prices_base, min_comparables=min_comp)
    sufficient_data = stats.get("sufficient_data", False)

    price_range = None
    median_val = None
    if sufficient_data:
        median_val = Decimal(str(stats["median"]))
        price_range = {
            "min": stats["min"],
            "median": stats["median"],
            "max": stats["max"],
            "comparables_count": stats["count"],
        }

    # 3. Evaluate each candidate
    evaluated_results = []
    for cand in normalized_candidates:
        price_base = cand["price_base"]

        # Deviation from median (strictly only if sufficient comparables exist)
        deviation_pct = None
        if median_val and median_val > 0:
            diff = price_base - median_val
            deviation_pct = round(float((diff / median_val) * 100), 1)

        # Criteria match
        match_score, passed_attr, missing_attr = calculate_criteria_match(
            buy_req.attrs, cand["attrs"]
        )

        # Distance estimation
        dist_km = estimate_distance_km(cand["location"], buyer_location)

        # Build verdict
        verdict = "FAIR"
        if match_score < 0.5:
            verdict = "CRITERIA_MISMATCH"
        elif buy_req.budget and buy_req.budget > 0 and price_base > buy_req.budget:
            verdict = "EXCEEDS_BUDGET"
        elif deviation_pct is not None:
            if deviation_pct <= -15.0 and match_score >= 0.8:
                verdict = "BARGAIN"
            elif deviation_pct >= 15.0:
                verdict = "OVERPRICED"
            else:
                verdict = "FAIR"
        else:
            if buy_req.budget and buy_req.budget > 0:
                verdict = "WITHIN_BUDGET" if price_base <= buy_req.budget else "EXCEEDS_BUDGET"
            else:
                verdict = "FAIR"

        evaluated_results.append({
            "external_url": cand["url"],
            "price_base": str(price_base),
            "currency": base_curr,
            "deviation_from_median_pct": deviation_pct,
            "criteria_match_score": match_score,
            "criteria_passed": passed_attr,
            "criteria_missing": missing_attr,
            "distance_km": dist_km,
            "verdict": verdict,
        })

    record_audit_event(
        session=session,
        actor=actor,
        action="CANDIDATES_EVALUATED",
        entity="buy_request",
        entity_id=buy_req.id,
        data={
            "candidates_count": len(evaluated_results),
            "sufficient_data": sufficient_data,
            "has_price_range": price_range is not None,
        },
    )
    record_truth(
        session=session,
        claim="CANDIDATE_EVALUATION_COMPLETED",
        recipient="OWNER",
        evidence={
            "request_id": buy_req.id,
            "candidates_evaluated": len(evaluated_results),
            "sufficient_data": sufficient_data,
            "min_comparables_applied": min_comp,
        },
    )
    session.flush()

    return {
        "request_id": buy_req.id,
        "base_currency": base_curr,
        "sample_size": len(prices_base),
        "min_comparables": min_comp,
        "sufficient_data": sufficient_data,
        "price_range": price_range,
        "candidates": evaluated_results,
    }
