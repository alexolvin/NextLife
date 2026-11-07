"""OpenClaw-skill HTTP API (item 3): media, comms, deals, canaries.

Part two of the /api/oc namespace (module length rule VI.1.1, <= 400 lines;
part one: routes_openclaw.py). Same authentication: NEXTLIFE_OPENCLAW_TOKEN
only (check_openclaw_auth), no web-token fallback.

Approval gate (owner rule 2026-10-04): `POST /api/oc/deals/close` requires a
current draft approval when the item has an ACTIVE listing — closing removes
the published listing; archiving an item without active listings is a local
SOLD->ARCHIVED transition and is not gated.
"""

import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.api.routes_openclaw import check_openclaw_auth, require_current_approval
from nextlife.canaries.engine import run_search_canary
from nextlife.comms.inbox import get_pending_inbox, handle_inbox_push
from nextlife.comms.outbox import OutboxError, ack_outbox, pull_outbox
from nextlife.connectors.base import ConnectorConfigError
from nextlife.connectors.registry import sync_marketplaces
from nextlife.core.config import get_media_dir, load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.media.jobs import sync_tier_models_and_generate_variants
from nextlife.media.pipeline import (
    PhotoKitAssemblyError,
    assemble_marketplace_kit,
    deterministic_process,
    ingest_photo,
    sanitize_photo,
)
from nextlife.services.deals import DealServiceError, archive_item_with_confirmation
from nextlife.storage.db import get_db_session
from nextlife.storage.models import ChannelCanary, Item, Listing, Marketplace, OwnerNotification

oc_ops_router = APIRouter(prefix="/api/oc", tags=["openclaw"])
_AUTH = Depends(check_openclaw_auth)


class OcAckBody(BaseModel):
    delivery_evidence: dict[str, Any]


class OcDealCloseBody(BaseModel):
    code: str


class OcCanaryRunBody(BaseModel):
    key: str | None = None


def _get_item(session: Session, code: str) -> Item:
    item = session.get(Item, code)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found: {code}")
    return item


@oc_ops_router.post("/items/{code}/photos")
def oc_media_ingest(
    code: str,
    files: list[UploadFile] = File(...),  # noqa: B008
    role: str = Form("ORIGINAL"),
    ai_generated: str = Form("false"),
    vlm_verdict: str = Form(""),
    auth: bool = _AUTH,
) -> dict[str, Any]:
    """Multipart photo ingest: ingest -> sanitize -> deterministic resize.

    Extra fields vs the web endpoint (owner rule, item 3): `ai_generated`
    (marks all derived copies; a photo declared as a DEFECT is never marked
    AI — the kit requires unretouched non-AI defect coverage) and
    `vlm_verdict` (agent note recorded on the original).
    """
    if not files:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one file must be provided")
    role = (role or "ORIGINAL").upper()
    ai_claim = str(ai_generated).strip().lower() in ("1", "true", "yes")
    actual_ai = ai_claim and role != "DEFECT"
    note = (vlm_verdict or "").strip()

    temp_dir = get_media_dir() / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    ingested: list[str] = []
    with get_db_session() as session:
        _get_item(session, code)
        try:
            for upload in files:
                filename = upload.filename or f"upload_{uuid.uuid4().hex[:6]}.jpg"
                temp_path = temp_dir / f"tmp_{uuid.uuid4().hex}_{filename}"
                try:
                    with open(temp_path, "wb") as f_out:
                        shutil.copyfileobj(upload.file, f_out)

                    orig = ingest_photo(session=session, item_code=code, src_path=temp_path, role=role)
                    orig.ai_generated = actual_ai
                    if note:
                        orig.vlm_verdict = {"source": "openclaw", "note": note}
                    clean = sanitize_photo(session=session, original=orig)
                    proc = deterministic_process(session=session, photo=clean)
                    clean.ai_generated = actual_ai
                    proc.ai_generated = actual_ai
                    sync_tier_models_and_generate_variants(
                        session=session,
                        item_code=code,
                        orig_photo=orig,
                        proc_photo=proc,
                        role=role,
                    )
                    ingested.append(orig.id)
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass
        except NextLifeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "ok", "item_code": code, "ingested_count": len(ingested), "ids": ingested}


@oc_ops_router.post("/items/{code}/media-build")
def oc_media_build(code: str, auth: bool = _AUTH) -> dict[str, Any]:
    """Assemble the publishable photo kit for every configured marketplace."""
    cfg = load_config()
    with get_db_session() as session:
        item = _get_item(session, code)
        sync_marketplaces(session, cfg)
        defect_list = [str(d) for d in (item.defects or [])]
        kits: dict[str, Any] = {}
        for mp_key, mp_cfg in cfg.marketplaces.items():
            mp_row = session.get(Marketplace, mp_key)
            entry: dict[str, Any] = {"enabled": bool(mp_row.enabled) if mp_row else False}
            try:
                photos = assemble_marketplace_kit(session, code, mp_cfg.capabilities, defect_list)
                entry["photo_count"] = len(photos)
                entry["photos"] = [
                    {
                        "id": p.id,
                        "role": p.role,
                        "ai_generated": p.ai_generated,
                        "width": p.width,
                        "height": p.height,
                    }
                    for p in photos
                ]
            except PhotoKitAssemblyError as exc:
                entry["error"] = str(exc)
            kits[mp_key] = entry
        if not any("error" not in v for v in kits.values()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No marketplace photo kit could be assembled for item {code}.",
            )
    return {"status": "ok", "item_code": code, "kits": kits}


@oc_ops_router.get("/outbox")
def oc_outbox_pull(auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        messages = pull_outbox(session)
    return {"status": "ok", "count": len(messages), "messages": messages}


@oc_ops_router.post("/outbox/{outbox_id}/ack")
def oc_outbox_ack(outbox_id: str, body: OcAckBody, auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        try:
            entry = ack_outbox(session, outbox_id, body.delivery_evidence)
            state = entry.state
        except OutboxError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "ok", "outbox_id": outbox_id, "state": state}


@oc_ops_router.post("/inbox")
def oc_inbox_push(payload: dict[str, Any], auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        result = handle_inbox_push(session, payload)
    return {"status": "ok", "result": result}


@oc_ops_router.get("/inbox/pending")
def oc_inbox_pending(auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        messages = get_pending_inbox(session)
    return {"status": "ok", "count": len(messages), "messages": messages}


@oc_ops_router.post("/deals/close")
def oc_deal_close(body: OcDealCloseBody, auth: bool = _AUTH) -> dict[str, Any]:
    code = body.code
    with get_db_session() as session:
        _get_item(session, code)
        active_listings = session.query(Listing).filter(
            Listing.item_code == code, Listing.status.in_(["PUBLISHED", "UPDATING"])
        ).count()
        if active_listings:
            require_current_approval(session, code)
        try:
            result = archive_item_with_confirmation(session, code)
        except DealServiceError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "ok", "item_code": code, "archive": result}


@oc_ops_router.post("/canaries/run")
def oc_canaries_run(body: OcCanaryRunBody, auth: bool = _AUTH) -> dict[str, Any]:
    """Run one live search probe (or all DIRECT channels when no key given)."""
    cfg = load_config()
    keys = [body.key] if body.key else [
        k for k, mp in cfg.marketplaces.items() if mp.access_mode == "DIRECT"
    ]
    results: list[dict[str, Any]] = []
    for key in keys:
        if key not in cfg.marketplaces:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown marketplace key: {key}")
        if cfg.marketplaces[key].access_mode != "DIRECT":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Marketplace '{key}' is not a DIRECT channel.",
            )
        with get_db_session() as session:
            try:
                res = run_search_canary(session, key, config=cfg)
            except ConnectorConfigError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        results.append(asdict(res))
    return {"status": "ok", "results": results}


@oc_ops_router.get("/canaries")
def oc_canaries_status(auth: bool = _AUTH) -> dict[str, Any]:
    with get_db_session() as session:
        rows = session.execute(select(ChannelCanary)).scalars().all()
        return {
            "status": "ok",
            "channels": [
                {
                    "marketplace_key": r.marketplace_key,
                    "scenario": r.scenario,
                    "channel": r.channel,
                    "state": r.state,
                    "block_reason": r.block_reason,
                    "last_attempt_at": r.last_attempt_at.isoformat() if r.last_attempt_at else None,
                    "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
                    "last_success_count": r.last_success_count,
                }
                for r in rows
            ],
        }


@oc_ops_router.get("/notifications")
def oc_pull_notifications(auth: bool = _AUTH) -> list[dict[str, Any]]:
    """Retrieve undelivered owner notifications (§7.3, §12)."""
    with get_db_session() as session:
        notifs = session.scalars(
            select(OwnerNotification)
            .where(OwnerNotification.delivered_at.is_(None))
            .order_by(OwnerNotification.created_at.asc())
        ).all()
        return [
            {
                "id": n.id,
                "at": n.created_at.isoformat() if n.created_at else None,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "url": n.url,
            }
            for n in notifs
        ]


@oc_ops_router.post("/notifications/{notif_id}/ack")
def oc_ack_notification(notif_id: str, auth: bool = _AUTH) -> dict[str, Any]:
    """Acknowledge delivery of owner notification (§7.3, §12)."""
    with get_db_session() as session:
        notif = session.get(OwnerNotification, notif_id)
        if not notif:
            raise HTTPException(status_code=404, detail="Notification not found")
        now = datetime.now(UTC)
        notif.delivered_at = now
        session.commit()
        return {
            "status": "ok",
            "id": notif_id,
            "delivered_at": now.isoformat(),
        }
