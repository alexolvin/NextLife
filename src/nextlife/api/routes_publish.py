"""Routes for publishing lots to marketplaces (C-37, C-42).

Enforces mandatory owner approval verification and access checks before publishing.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from nextlife.api.routes import check_auth
from nextlife.core.approval import has_valid_approval
from nextlife.core.status import ItemStatus
from nextlife.services.publish import publish_lot
from nextlife.services.sell import build_draft
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Lot

publish_router = APIRouter(prefix="/api/lots", tags=["publish"])


class PublishRequest(BaseModel):
    marketplace: str | None = None
    marketplaces: list[str] | None = None
    dry_run: bool = False


@publish_router.post("/{code}/publish")
def api_publish_lot(
    code: str,
    payload: PublishRequest | None = None,
    auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    with get_db_session() as session:
        item = session.scalar(select(Item).where(Item.code == code))
        if not item:
            raise HTTPException(status_code=404, detail="Lot/Item not found")

        # 1. Verification of draft approval (Rule П-1.1, Section 17)
        if item.status != ItemStatus.APPROVED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Publication rejected: lot {code} is in status '{item.status}', must be 'APPROVED' before publication.",
            )

        draft = build_draft(session, item.code)
        canonical_hash = draft["payload_hash"]
        if not has_valid_approval(session, item.code, canonical_hash):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Publication rejected: draft payload for lot {code} has not been approved or was modified after approval.",
            )

        # 2. Execute publishing
        target_mp = payload.marketplace if payload and payload.marketplace else None
        target_mps = [target_mp] if target_mp else ((payload.marketplaces if payload else None) or ["bazos_cz"])
        dry_run = payload.dry_run if payload else False

        lot = session.scalar(select(Lot).where(Lot.code == code))
        if not lot:
            lot = Lot(
                code=code,
                item_id=code,
                status=item.status,
                price_gross=item.target_price,
                currency=item.target_currency or "CZK",
            )
            session.add(lot)
            session.flush()

        res = publish_lot(
            session=session,
            lot_code=code,
            target_marketplaces=target_mps,
            dry_run=dry_run,
        )
        return res
