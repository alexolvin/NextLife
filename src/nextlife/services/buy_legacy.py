"""Legacy buy search and contact service helpers for tests and backwards compatibility.

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from nextlife.connectors.base import BaseConnector
from nextlife.core.config import ConfigBundle, load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.money.money import convert_price, format_price_display
from nextlife.obs.truth_log import record_truth
from nextlife.services.research import calculate_price_stats
from nextlife.storage.models import BuyCandidate, BuyRequest


class BuyLegacyError(NextLifeError):
    """Base error for buying legacy operations."""


def create_buy_request(
    session: Session,
    query: str,
    budget: Decimal | None = None,
    currency: str = "EUR",
    attrs: dict[str, Any] | None = None,
) -> BuyRequest:
    req = BuyRequest(
        id=str(uuid.uuid4()),
        query=query,
        budget=budget or Decimal("0.00"),
        currency=currency,
        attrs=attrs or {},
        status="SEARCHING",
    )
    session.add(req)
    session.flush()
    return req


def execute_buy_search(
    session: Session,
    buy_request_id: str,
    connectors: dict[str, BaseConnector],
    config: ConfigBundle | None = None,
) -> dict[str, Any]:
    """Execute search across marketplaces, convert currencies, and produce buy report (Section 10 Ш3)."""
    cfg = config or load_config()
    req = session.get(BuyRequest, buy_request_id)
    if not req:
        raise BuyLegacyError(f"Buy request not found: {buy_request_id}")

    candidates: list[dict[str, Any]] = []
    today = datetime.now(UTC).date()

    for mp_key, conn in connectors.items():
        try:
            results = conn.search(req.query)
        except Exception:
            continue

        for r in results:
            price_triple = convert_price(session, r.price, r.currency, today, cfg.app.base_currency)
            verdict = "Ниже бюджета" if price_triple.amount_base <= (req.budget or Decimal("0.00")) else "Выше бюджета"

            cand = BuyCandidate(
                id=str(uuid.uuid4()),
                request_id=req.id,
                marketplace_key=mp_key,
                external_url=r.external_url,
                price=r.price,
                currency=r.currency,
                price_base=price_triple.amount_base,
                verdict=verdict,
                contacted=False,
            )
            session.add(cand)

            formatted_repr = format_price_display(price_triple, cfg.app.base_currency)
            candidates.append({
                "id": cand.id,
                "marketplace": mp_key,
                "url": r.external_url,
                "price": str(r.price),
                "currency": r.currency,
                "price_base": str(price_triple.amount_base),
                "formatted": formatted_repr,
                "verdict": verdict,
            })

    session.flush()

    min_comp = cfg.app.min_comparables
    prices_base = [Decimal(c["price_base"]) for c in candidates]
    stats = calculate_price_stats(prices_base, min_comparables=min_comp)

    req.status = "CANDIDATES_FOUND" if candidates else "NO_RESULTS"
    session.flush()

    return {
        "request_id": req.id,
        "query": req.query,
        "budget": str(req.budget),
        "total_candidates": len(candidates),
        "price_stats": stats,
        "candidates": candidates,
    }


def contact_seller(
    session: Session,
    candidate_id: str,
    questions: list[str],
) -> dict[str, Any]:
    """Agent contacts seller on behalf of owner without binding commitments (Section 10 Ш4)."""
    cand = session.get(BuyCandidate, candidate_id)
    if not cand:
        raise BuyLegacyError(f"Candidate not found: {candidate_id}")

    cand.contacted = True
    record_truth(
        session=session,
        claim=f"Запрос продавцу по кандидату {cand.external_url}: {len(questions)} вопросов",
        recipient="COUNTERPARTY",
        evidence={"candidate_id": cand.id, "questions": questions, "url": cand.external_url},
    )
    session.flush()
    return {"status": "contacted", "candidate_id": cand.id, "questions": questions}
