"""Dispatch evidence management and completeness verification (TZ v2.0 Section III.6 / Task T-066).

DoD:
- Four mandatory dispatch evidences:
  1. box_content_photo
  2. serial_number_photo
  3. sealed_box_label_photo
  4. tracking_and_weight_receipt
- Verification of evidence completeness.
- Items can be dispatched without full evidence set, but are explicitly flagged as evidence_incomplete.
- Outgoing filter verifies zero leakage.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.services.fulfillment import release_tracking_number
from nextlife.storage.models import Deal, Handover, Item, Lot


def _storage_location_for_deal(session: Session, deal: Deal) -> str | None:
    """ТЗ «Склад» §3.6: место на складе предмета сделки."""
    if not deal.lot_id:
        return None
    lot = session.get(Lot, deal.lot_id)
    if not lot:
        return None
    item = session.get(Item, lot.item_id)
    return item.storage_location if item else None

MANDATORY_EVIDENCES = [
    "box_content_photo",
    "serial_number_photo",
    "sealed_box_label_photo",
    "tracking_and_weight_receipt",
]

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
NEXTLIFE_CONFIG_PATH = BASE_DIR / "config" / "nextlife.yaml"


class DispatchEvidenceError(NextLifeError):
    """Raised on invalid dispatch evidence or state."""


def get_insurance_threshold() -> Decimal:
    """Read insurance_required_above from nextlife.yaml config."""
    if NEXTLIFE_CONFIG_PATH.exists():
        with open(NEXTLIFE_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            val = data.get("insurance_required_above")
            if val is not None:
                return Decimal(str(val))
    return Decimal("5000.00")


def submit_dispatch_evidence(
    session: Session,
    deal_id: str,
    evidence_type: str,
    file_path_or_url: str,
    metadata: dict[str, Any] | None = None,
    actor: str = "OWNER",
) -> dict[str, Any]:
    """Attach one of the required dispatch evidences to a deal handover."""
    deal = session.get(Deal, deal_id)
    if not deal:
        raise DispatchEvidenceError(f"Deal '{deal_id}' not found")

    handover = session.query(Handover).filter(Handover.deal_id == deal.id).first()
    if not handover:
        handover = Handover(
            id=str(uuid.uuid4()),
            deal_id=deal.id,
            method=deal.delivery_method or "CARRIER",
            status="PENDING",
            evidence={},
        )
        session.add(handover)
        session.flush()

    ev = dict(handover.evidence or {})
    dispatch_ev = dict(ev.get("dispatch_evidence") or {})

    entry = {
        "file_path": file_path_or_url,
        "submitted_at": datetime.now(UTC).isoformat(),
        "submitted_by": actor,
        "metadata": metadata or {},
    }
    dispatch_ev[evidence_type] = entry
    ev["dispatch_evidence"] = dispatch_ev

    # Re-evaluate completeness
    present = [k for k in MANDATORY_EVIDENCES if k in dispatch_ev]
    missing = [k for k in MANDATORY_EVIDENCES if k not in dispatch_ev]
    is_complete = len(missing) == 0

    ev["evidence_incomplete"] = not is_complete
    handover.evidence = ev

    record_audit_event(
        session=session,
        actor=actor,
        action="DISPATCH_EVIDENCE_SUBMITTED",
        entity="handover",
        entity_id=handover.id,
        data={
            "deal_id": deal.id,
            "evidence_type": evidence_type,
            "file_path": file_path_or_url,
            "is_complete": is_complete,
        },
    )
    session.flush()

    return {
        "deal_id": deal.id,
        "handover_id": handover.id,
        "evidence_type": evidence_type,
        "is_complete": is_complete,
        "present_count": len(present),
        "missing": missing,
        "storage_location": _storage_location_for_deal(session, deal),
    }


def get_evidence_status(session: Session, deal_id: str) -> dict[str, Any]:
    """Retrieve completeness status of dispatch evidence for a deal."""
    deal = session.get(Deal, deal_id)
    if not deal:
        raise DispatchEvidenceError(f"Deal '{deal_id}' not found")

    handover = session.query(Handover).filter(Handover.deal_id == deal.id).first()
    ev = dict(handover.evidence or {}) if handover else {}
    dispatch_ev = dict(ev.get("dispatch_evidence") or {})

    present = [k for k in MANDATORY_EVIDENCES if k in dispatch_ev]
    missing = [k for k in MANDATORY_EVIDENCES if k not in dispatch_ev]
    is_complete = len(missing) == 0

    return {
        "deal_id": deal.id,
        "handover_id": handover.id if handover else None,
        "is_complete": is_complete,
        "evidence_incomplete": not is_complete,
        "present": present,
        "missing": missing,
        "details": dispatch_ev,
    }


def dispatch_deal(
    session: Session,
    deal_id: str,
    tracking_number: str | None = None,
    carrier: str = "Zásilkovna",
    tracking_url: str | None = None,
    actor: str = "OWNER",
) -> dict[str, Any]:
    """Dispatch deal parcel with evidence completeness determination."""
    deal = session.get(Deal, deal_id)
    if not deal:
        raise DispatchEvidenceError(f"Deal '{deal_id}' not found")

    status_res = get_evidence_status(session, deal_id)
    is_incomplete = status_res["evidence_incomplete"]

    handover = session.query(Handover).filter(Handover.deal_id == deal.id).first()
    if not handover:
        handover = Handover(
            id=str(uuid.uuid4()),
            deal_id=deal.id,
            method="CARRIER",
            status="PENDING",
            evidence={},
        )
        session.add(handover)
        session.flush()

    ev = dict(handover.evidence or {})
    ev["evidence_incomplete"] = is_incomplete
    ev["dispatched_at"] = datetime.now(UTC).isoformat()
    handover.evidence = ev
    handover.status = "DISPATCHED"

    if tracking_number:
        handover.tracking_number = tracking_number
    if tracking_url:
        handover.tracking_url = tracking_url

    # Check insurance requirement
    insurance_threshold = get_insurance_threshold()
    requires_insurance = deal.agreed_price >= insurance_threshold

    # Record truth assertion
    record_truth(
        session=session,
        claim="PARCEL_DISPATCHED",
        recipient="OWNER",
        evidence={
            "deal_id": deal.id,
            "evidence_incomplete": is_incomplete,
            "missing_evidence": status_res["missing"],
            "requires_insurance": requires_insurance,
            "tracking_number": tracking_number,
        },
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="DEAL_DISPATCHED",
        entity="deal",
        entity_id=deal.id,
        data={
            "evidence_incomplete": is_incomplete,
            "missing": status_res["missing"],
            "tracking_number": tracking_number,
            "requires_insurance": requires_insurance,
        },
    )

    # Release tracking to counterparty if available
    release_info = None
    if tracking_number and (deal.status == "PAID" or (deal.payment_method or "").upper() in ("COD", "DOBIRKA")):
        release_info = release_tracking_number(
            session=session,
            deal_id=deal.id,
            tracking_number=tracking_number,
            carrier=carrier,
            tracking_url=tracking_url,
            actor=actor,
        )

    session.flush()

    return {
        "deal_id": deal.id,
        "handover_id": handover.id,
        "handover_status": handover.status,
        "evidence_incomplete": is_incomplete,
        "missing_evidences": status_res["missing"],
        "requires_insurance": requires_insurance,
        "tracking_released": release_info is not None,
        "storage_location": _storage_location_for_deal(session, deal),
    }
