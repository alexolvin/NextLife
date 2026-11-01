"""Meeting places and safety enforcement service (TZ v2.0 Section III.5.2).

DoD:
- Selection of verified public meeting place from config/meeting_places.yaml.
- Strict prohibition against releasing private home address without explicit override.
- High value warning triggered when deal amount exceeds high_value_meeting_threshold.
- Generation of calendar event (.ics) for owner with lot code, time, place, amount.
- Outgoing filter verifies zero leakage of forbidden fields (home_address, price_floor, etc.).
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Listing, Lot, Message, Thread

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"
MEETING_PLACES_PATH = CONFIG_DIR / "meeting_places.yaml"
NEXTLIFE_CONFIG_PATH = CONFIG_DIR / "nextlife.yaml"
COMMS_DIR = BASE_DIR / "templates" / "comms"
PROPOSAL_TEMPLATE_PATH = COMMS_DIR / "meeting_proposal.jinja2"
WARNING_TEMPLATE_PATH = COMMS_DIR / "meeting_high_value_warning.jinja2"

DEFAULT_HIGH_VALUE_THRESHOLD = Decimal("5000.00")


class MeetingSafetyError(NextLifeError):
    """Raised on safety violation or unverified meeting arrangement."""


class HomeAddressProhibitedError(NextLifeError):
    """Raised when private home address is attempted to be released."""


def get_high_value_threshold() -> Decimal:
    """Retrieve high-value meeting threshold from configuration."""
    if NEXTLIFE_CONFIG_PATH.exists():
        with open(NEXTLIFE_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            val = data.get("high_value_meeting_threshold")
            if val is not None:
                return Decimal(str(val))
    return DEFAULT_HIGH_VALUE_THRESHOLD


def load_meeting_places() -> list[dict[str, Any]]:
    """Load verified public meeting spots from configuration."""
    if not MEETING_PLACES_PATH.exists():
        return []
    with open(MEETING_PLACES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("places", []))


def get_meeting_place_by_id(place_id: str) -> dict[str, Any] | None:
    """Look up a meeting place by its unique ID."""
    places = load_meeting_places()
    for p in places:
        if p.get("id") == place_id:
            return p
    return None


def is_high_value_deal(amount: Decimal | float | int) -> bool:
    """Check if transaction amount exceeds high-value threshold."""
    threshold = get_high_value_threshold()
    return Decimal(str(amount)) >= threshold


def render_meeting_proposal(
    place: dict[str, Any],
    meeting_time: str,
    lang: str = "cs",
    notes: str | None = None,
    allow_home_address: bool = False,
) -> str:
    """Render meeting proposal message with strict address security."""
    if not allow_home_address and place.get("safety_level") == "PRIVATE_HOME":
        raise HomeAddressProhibitedError(
            "Private home address cannot be proposed without explicit owner authorization."
        )

    place_name = place.get(f"name_{lang}") or place.get("name_cs") or place.get("name_ru") or place.get("id", "")
    place_address = place.get("address", "")

    context: dict[str, Any] = {
        "lang": lang,
        "place_name": place_name,
        "place_address": place_address,
        "meeting_time": meeting_time,
        "notes": notes,
    }

    # Protect against leakage of forbidden fields
    check_context_for_internal_fields(context)

    if not PROPOSAL_TEMPLATE_PATH.exists():
        raise MeetingSafetyError(f"Proposal template missing: {PROPOSAL_TEMPLATE_PATH}")

    template_str = PROPOSAL_TEMPLATE_PATH.read_text(encoding="utf-8")
    return render_outgoing_template(template_str, context)


def render_high_value_warning(
    lot_code: str,
    amount: Decimal | float,
    currency: str = "CZK",
    recommended_place: str | None = None,
) -> str:
    """Render safety warning for the owner regarding high-value deals."""
    threshold = get_high_value_threshold()
    dec_amount = Decimal(str(amount))

    if not recommended_place:
        places = load_meeting_places()
        recommended_place = places[0]["name_ru"] if places else "Центральный ТЦ или отделение полиции"

    context = {
        "lot_code": lot_code,
        "amount": dec_amount,
        "currency": currency,
        "threshold": threshold,
        "recommended_place": recommended_place,
    }

    check_context_for_internal_fields(context)

    if not WARNING_TEMPLATE_PATH.exists():
        raise MeetingSafetyError(f"Warning template missing: {WARNING_TEMPLATE_PATH}")

    template_str = WARNING_TEMPLATE_PATH.read_text(encoding="utf-8")
    return render_outgoing_template(template_str, context)


def generate_calendar_ics(
    lot_code: str,
    place_name: str,
    place_address: str,
    start_dt: datetime,
    amount: Decimal | float,
    currency: str = "CZK",
    counterparty: str = "Buyer",
    duration_minutes: int = 30,
) -> str:
    """Generate RFC 5545 iCalendar (.ics) string for the owner's calendar."""
    uid = f"nextlife-{lot_code}-{start_dt.strftime('%Y%m%d%H%M%S')}@nextlife.cz"
    dtstamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dtstart = start_dt.strftime("%Y%m%dT%H%M%SZ")
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    dtend = end_dt.strftime("%Y%m%dT%H%M%SZ")

    summary = f"NextLife Встреча [{lot_code}] - {place_name}"
    description = (
        f"Лот: {lot_code}\\n"
        f"Сумма: {amount} {currency}\\n"
        f"Место: {place_name} ({place_address})\\n"
        f"Покупатель: {counterparty}"
    )

    ics_content = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//NextLife//Meeting Safety V1.0//RU\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{dtstamp}\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"DTEND:{dtend}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{description}\r\n"
        f"LOCATION:{place_address}\r\n"
        "STATUS:CONFIRMED\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    return ics_content


def propose_meeting_in_thread(
    session: Session,
    thread_id: str,
    place_id: str,
    meeting_time_str: str,
    start_dt: datetime | None = None,
    lang: str = "cs",
    notes: str | None = None,
    actor: str = "SYSTEM",
) -> dict[str, Any]:
    """Execute full meeting proposal flow with safety checks and calendar creation."""
    thread = session.get(Thread, thread_id)
    if not thread:
        raise MeetingSafetyError(f"Thread {thread_id} not found")

    place = get_meeting_place_by_id(place_id)
    if not place:
        raise MeetingSafetyError(f"Meeting place '{place_id}' is not an approved public spot")

    lot_code = "UNKNOWN"
    amount = Decimal("0.00")
    if thread.listing_id:
        listing = session.get(Listing, thread.listing_id)
        if listing and listing.item_code:
            lot = session.query(Lot).filter(Lot.item_id == listing.item_code).first()
            if lot:
                lot_code = lot.code
                amount = lot.price_gross or Decimal("0.00")

    # Render proposal for counterparty
    proposal_text = render_meeting_proposal(
        place=place,
        meeting_time=meeting_time_str,
        lang=lang,
        notes=notes,
    )

    msg = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        direction="OUT",
        transport=thread.channel if thread.channel in ("MARKETPLACE", "EMAIL", "TELEGRAM") else "MARKETPLACE",
        text_original=proposal_text,
        lang_original=lang,
        sent_at=datetime.now(UTC),
        template_key="meeting_proposal",
        source_evidence={"place_id": place_id, "meeting_time": meeting_time_str},
    )
    session.add(msg)

    buyer_outbox = enqueue_outbox(
        session=session,
        transport="EMAIL" if thread.channel == "EMAIL" else "TELEGRAM",
        recipient_hint=thread.counterparty,
        body=proposal_text,
        idempotency_key=f"meeting-prop-{thread.id}-{uuid.uuid4().hex[:8]}",
    )

    owner_warning_outbox = None
    if is_high_value_deal(amount):
        warning_text = render_high_value_warning(
            lot_code=lot_code,
            amount=amount,
            recommended_place=place.get("name_ru"),
        )
        owner_warning_outbox = enqueue_outbox(
            session=session,
            transport="TELEGRAM",
            recipient_hint="OWNER",
            body=warning_text,
            idempotency_key=f"meeting-warn-{thread.id}-{uuid.uuid4().hex[:8]}",
        )

    # Calendar event
    ics_text = None
    if start_dt:
        ics_text = generate_calendar_ics(
            lot_code=lot_code,
            place_name=str(place.get("name_ru") or place.get("id") or "Public Place"),
            place_address=place.get("address", ""),
            start_dt=start_dt,
            amount=amount,
            counterparty=thread.counterparty,
        )

    record_audit_event(
        session=session,
        actor=actor,
        action="MEETING_PROPOSED",
        entity="thread",
        entity_id=thread.id,
        data={
            "place_id": place_id,
            "meeting_time": meeting_time_str,
            "high_value": is_high_value_deal(amount),
            "amount": float(amount),
        },
    )
    record_truth(
        session=session,
        claim="MEETING_LOCATION_PROPOSED",
        recipient=thread.counterparty,
        evidence={
            "place_id": place_id,
            "is_public": place.get("safety_level") != "PRIVATE_HOME",
            "thread_id": thread.id,
        },
    )
    session.flush()

    return {
        "message_id": msg.id,
        "buyer_outbox_id": buyer_outbox.id,
        "owner_warning_id": owner_warning_outbox.id if owner_warning_outbox else None,
        "calendar_ics": ics_text,
    }
