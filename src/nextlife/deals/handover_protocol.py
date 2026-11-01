"""Handover protocol (acceptance act) generator (TZ v2.0 Section III.5.3 / Task T-065).

DoD:
- Bilingual document (Russian & Czech).
- Mandatory fields: parties, date/place, item, serial number, condition & defects,
  price & payment method, buyer inspection confirmation, accessory completeness, signatures.
- Stored in handover.protocol_path and deal evidence.
- Context checked by outgoing filter against internal field leakage.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.config import get_data_dir
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Deal, Handover, Item, ItemDefect, Lot, Party

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "templates" / "deals" / "handover_protocol.jinja2"


class HandoverProtocolError(NextLifeError):
    """Raised when handover protocol generation or persistence fails."""


def generate_handover_protocol(
    session: Session,
    deal_id: str,
    place: str | None = None,
    seller_name: str = "NextLife Owner",
    base_storage_dir: Path | None = None,
    actor: str = "SYSTEM",
) -> tuple[str, Path]:
    """Generate and persist bilingual handover protocol for a deal."""
    deal = session.get(Deal, deal_id)
    if not deal:
        raise HandoverProtocolError(f"Deal {deal_id} not found")

    lot = session.get(Lot, deal.lot_id) if deal.lot_id else None
    lot_code = lot.code if lot else "N/A"

    item: Item | None = None
    if lot and lot.item_id:
        item = session.get(Item, lot.item_id)

    party: Party | None = None
    if deal.party_id:
        party = session.get(Party, deal.party_id)

    handover = session.query(Handover).filter(Handover.deal_id == deal.id).first()
    if not handover:
        handover = Handover(
            id=str(uuid.uuid4()),
            deal_id=deal.id,
            method="PERSONAL" if "PICKUP" in (deal.delivery_method or "") else deal.delivery_method,
            status="PENDING",
            place=place or "Praha",
        )
        session.add(handover)
        session.flush()

    resolved_place = place or handover.place or "Praha, Veřejné místo předání"
    buyer_name = party.name if (party and party.name) else "Kupující (Osobní převzetí)"
    buyer_contact = party.email or party.phone or "Osobní kontakt" if party else "N/A"

    serial_num = item.serial_number if (item and item.serial_number) else "Neuvedeno / N/A"
    item_title = item.title if item else ((lot.notes if lot else None) or f"Лот {lot_code}")
    category = item.category_key if item else "General"
    condition = item.condition_grade if (item and item.condition_grade) else "Použité / Б/У"

    # Defect list
    defects_list = []
    if item:
        db_defects = session.query(ItemDefect).filter(ItemDefect.item_code == item.code).all()
        for d in db_defects:
            defects_list.append(f"[{d.defect_type.upper()}] {d.description_cs} / {d.description_owner}")

    now_dt = datetime.now(UTC)
    date_str = now_dt.strftime("%d.%m.%Y %H:%M")

    context: dict[str, Any] = {
        "date_str": date_str,
        "place": resolved_place,
        "lot_code": lot_code,
        "deal_id": deal.id,
        "seller_name": seller_name,
        "buyer_name": buyer_name,
        "buyer_contact": buyer_contact,
        "item_title": item_title,
        "category": category,
        "serial_number": serial_num,
        "condition": condition,
        "defects": defects_list,
        "price": str(deal.agreed_price),
        "currency": deal.currency,
        "payment_method": deal.payment_method,
        "payment_status": deal.status,
    }

    # Verify context against internal field leakage
    check_context_for_internal_fields(context)

    if not TEMPLATE_PATH.exists():
        raise HandoverProtocolError(f"Handover protocol template missing: {TEMPLATE_PATH}")

    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered_text = render_outgoing_template(template_str, context)

    # Persist file
    storage_root = base_storage_dir or (get_data_dir() / "deals")
    target_dir = storage_root / deal.id
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"handover_protocol_{lot_code}.md"
    file_path.write_text(rendered_text, encoding="utf-8")

    # Update handover record
    handover.protocol_path = str(file_path)
    ev = dict(handover.evidence or {})
    ev["protocol_path"] = str(file_path)
    ev["generated_at"] = now_dt.isoformat()
    ev["serial_number"] = serial_num
    ev["defects_count"] = len(defects_list)
    handover.evidence = ev

    record_audit_event(
        session=session,
        actor=actor,
        action="HANDOVER_PROTOCOL_GENERATED",
        entity="handover",
        entity_id=handover.id,
        data={
            "deal_id": deal.id,
            "lot_code": lot_code,
            "protocol_path": str(file_path),
            "defects_count": len(defects_list),
        },
    )
    record_truth(
        session=session,
        claim="HANDOVER_PROTOCOL_CREATED",
        recipient=buyer_contact,
        evidence={
            "deal_id": deal.id,
            "lot_code": lot_code,
            "serial_number": serial_num,
            "path": str(file_path),
        },
    )
    session.flush()

    return rendered_text, file_path


def get_handover_protocol(session: Session, deal_id: str) -> str | None:
    """Retrieve content of handover protocol if it exists."""
    handover = session.query(Handover).filter(Handover.deal_id == deal_id).first()
    if not handover or not handover.protocol_path:
        return None

    proto_path = Path(handover.protocol_path)
    if not proto_path.exists():
        return None

    return proto_path.read_text(encoding="utf-8")
