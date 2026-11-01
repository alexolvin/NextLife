"""API routes for deal dispatch evidence and dispatch execution (TZ v2.0 Section III.6 / Task T-066).

DoD:
- Four mandatory dispatch evidences.
- Verification of evidence completeness.
- Items dispatched without full set flagged evidence_incomplete.
- Zero raw HTML strings in code.
- Module length <= 400 lines.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from nextlife.api.routes import check_auth
from nextlife.core.exceptions import NextLifeError
from nextlife.services.dispatch_evidence import (
    dispatch_deal,
    get_evidence_status,
    submit_dispatch_evidence,
)
from nextlife.storage.db import get_db_session

evidence_router = APIRouter(tags=["deal-evidence"])


class SubmitEvidencePayload(BaseModel):
    evidence_type: str
    file_path: str
    metadata: dict[str, Any] | None = None


class DispatchPayload(BaseModel):
    tracking_number: str | None = None
    carrier: str = "Zásilkovna"
    tracking_url: str | None = None


@evidence_router.post("/api/deals/{deal_id}/evidence")
def api_submit_evidence(
    deal_id: str,
    payload: SubmitEvidencePayload,
    authenticated: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Upload or submit an evidence piece for deal dispatch."""
    with get_db_session() as session:
        try:
            result = submit_dispatch_evidence(
                session=session,
                deal_id=deal_id,
                evidence_type=payload.evidence_type,
                file_path_or_url=payload.file_path,
                metadata=payload.metadata,
                actor="OWNER",
            )
            session.commit()
            return result
        except NextLifeError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@evidence_router.get("/api/deals/{deal_id}/evidence")
def api_get_evidence_status(
    deal_id: str,
    authenticated: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Retrieve completeness status of all dispatch evidence for a deal."""
    with get_db_session() as session:
        try:
            return get_evidence_status(session=session, deal_id=deal_id)
        except NextLifeError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@evidence_router.post("/api/deals/{deal_id}/dispatch")
def api_dispatch_deal(
    deal_id: str,
    payload: DispatchPayload | None = None,
    authenticated: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Mark deal as dispatched and verify evidence completeness."""
    p = payload or DispatchPayload()
    with get_db_session() as session:
        try:
            result = dispatch_deal(
                session=session,
                deal_id=deal_id,
                tracking_number=p.tracking_number,
                carrier=p.carrier,
                tracking_url=p.tracking_url,
                actor="OWNER",
            )
            session.commit()
            return result
        except NextLifeError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
