"""Web routes and API endpoints for Buy section and kit analysis (TZ §8).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select

from nextlife.api.routes import check_auth, templates
from nextlife.services.buy import (
    BuyServiceError,
    analyze_kit,
    create_buy_search,
    format_candidates,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import BuyCandidate, BuyRequest

router = APIRouter(tags=["buy"])


@router.get("/ui/buy", response_class=HTMLResponse)
def screen_buy_index(
    request: Request,
    request_id: str | None = Query(default=None),
    condition: str = Query(default="all"),  # all / new / used
    offer_type: str = Query(default="all"),  # all / single / kit
    sort: str = Query(default="total_asc"),
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render Buy section with search, segments, past queries, and cards (§8.1)."""
    with get_db_session() as session:
        # Fetch past requests
        past_requests = session.scalars(
            select(BuyRequest).order_by(desc(BuyRequest.created_at)).limit(10)
        ).all()

        current_req = None
        if request_id:
            current_req = session.get(BuyRequest, request_id)
        elif past_requests:
            current_req = past_requests[0]

        candidates_raw = []
        if current_req:
            candidates_raw = session.scalars(
                select(BuyCandidate).where(BuyCandidate.request_id == current_req.id)
            ).all()

        formatted = format_candidates(
            candidates_raw,
            condition_filter=condition,
            type_filter=offer_type,
            sort=sort,
        )

    return templates.TemplateResponse(
        request=request,
        name="buy.html",
        context={
            "requests": past_requests,
            "current_request": current_req,
            "candidates": formatted,
            "condition": condition,
            "offer_type": offer_type,
            "sort": sort,
            "active_tab": "buy",
        },
    )


@router.post("/ui/buy/search")
def ui_create_buy_search(
    query: str = Form(""),
    currency: str = Form("CZK"),
    postal_code: str | None = Form(None),
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Trigger background buy search and redirect to results (§8.1)."""
    with get_db_session() as session:
        try:
            req = create_buy_search(
                session=session,
                query=query,
                currency=currency,
                postal_code=postal_code,
            )
            session.commit()
            return RedirectResponse(
                url=f"/ui/buy?request_id={req.id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        except BuyServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ui/buy/kits/{candidate_id}", response_class=HTMLResponse)
def screen_kit_analysis(
    request: Request,
    candidate_id: str,
    auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render dedicated kit analysis screen (§8.2.5)."""
    with get_db_session() as session:
        candidate = session.get(BuyCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Кандидат не найден")

        v = candidate.verdict or {}
        analysis = v.get("kit_analysis")
        if not analysis:
            # Run analysis automatically on first visit
            analysis = analyze_kit(session, candidate_id)
            session.commit()

    return templates.TemplateResponse(
        request=request,
        name="buy_kit.html",
        context={
            "candidate": candidate,
            "analysis": analysis,
            "active_tab": "buy",
        },
    )


@router.post("/ui/buy/kits/{candidate_id}/analyze")
def ui_reanalyze_kit(
    candidate_id: str,
    auth: bool = Depends(check_auth),
) -> RedirectResponse:
    """Rerun kit analysis (§8.2.5)."""
    with get_db_session() as session:
        analyze_kit(session, candidate_id)
        session.commit()
    return RedirectResponse(
        url=f"/ui/buy/kits/{candidate_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# --- JSON API ---


@router.post("/api/buy/search")
def api_buy_search(
    payload: dict[str, Any],
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    q = str(payload.get("query") or "").strip()
    curr = str(payload.get("currency") or "CZK")
    with get_db_session() as session:
        req = create_buy_search(session, query=q, currency=curr)
        session.commit()
        return {"status": "ok", "request_id": req.id}


@router.get("/api/buy/{request_id}")
def api_get_buy_results(
    request_id: str,
    condition: str = Query(default="all"),
    offer_type: str = Query(default="all"),
    sort: str = Query(default="total_asc"),
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    with get_db_session() as session:
        req = session.get(BuyRequest, request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        candidates = session.scalars(
            select(BuyCandidate).where(BuyCandidate.request_id == request_id)
        ).all()
        formatted = format_candidates(candidates, condition_filter=condition, type_filter=offer_type, sort=sort)
        return {
            "status": "ok",
            "request_id": req.id,
            "query": req.query,
            "count": len(formatted),
            "candidates": formatted,
        }


@router.post("/api/buy/kits/{candidate_id}/analyze")
def api_analyze_kit_endpoint(
    candidate_id: str,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    with get_db_session() as session:
        return analyze_kit(session, candidate_id)
