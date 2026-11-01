"""Inspection protocol service (TZ v2.0 Section III.5.1).

DoD:
- Checklist for GPU contains all nine points from TZ.
- Category without file falls back to config/inspection/default.yaml.
- Checklists available formatted for outgoing messages and UI.
- No raw HTML strings in Python source code (rendered via Jinja2 templates).
- Sensitive internal fields checked by outgoing filter.
- Line limit <= 400 lines.
"""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Template
from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Message, Outbox, Thread

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
INSPECTION_CONFIG_DIR = BASE_DIR / "config" / "inspection"
COMMS_TEMPLATE_PATH = BASE_DIR / "templates" / "comms" / "inspection_protocol.jinja2"
WEB_TEMPLATE_PATH = BASE_DIR / "templates" / "web" / "inspection_checklist.html"


class InspectionProtocolError(NextLifeError):
    """Raised when inspection protocol cannot be resolved or rendered."""


def normalize_category_key(category: str | None) -> str:
    """Normalize category string to configuration key."""
    if not category:
        return "default"
    cat = category.strip().lower()
    if cat in ("gpu", "graphics_card", "videocard", "video_card", "grafická karta", "видеокарта"):
        return "gpu"
    cleaned = re.sub(r"[^a-z0-9_]+", "_", cat).strip("_")
    return cleaned or "default"


def load_inspection_checklist(category: str | None = None) -> dict[str, Any]:
    """Load inspection checklist definition from YAML config.

    Falls back to default.yaml if specific category is not found.
    """
    key = normalize_category_key(category)
    cat_file = INSPECTION_CONFIG_DIR / f"{key}.yaml"
    if not cat_file.exists():
        cat_file = INSPECTION_CONFIG_DIR / "default.yaml"

    if not cat_file.exists():
        raise InspectionProtocolError(f"Default inspection configuration not found at {cat_file}")

    with open(cat_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data


def get_checklist_items(category: str | None = None, lang: str = "cs") -> list[dict[str, str]]:
    """Get localized inspection checklist items for a category."""
    data = load_inspection_checklist(category)
    raw_points = data.get("points", [])
    lang_key = "cs" if lang == "cs" else "ru"

    result = []
    for p in raw_points:
        text = p.get(lang_key) or p.get("ru") or p.get("cs") or ""
        secondary = p.get("cs" if lang_key == "ru" else "ru")
        result.append({
            "id": p.get("id", ""),
            "text": text,
            "secondary_text": secondary if secondary != text else "",
        })
    return result


def render_inspection_message(
    category: str | None = None,
    lang: str = "cs",
    extra_notes: str | None = None,
) -> str:
    """Render message protocol for outgoing buyer/seller communications."""
    data = load_inspection_checklist(category)
    items = get_checklist_items(category, lang=lang)

    cat_name = data.get("category_name_cs" if lang == "cs" else "category_name_ru") or data.get("category", "General")

    context: dict[str, Any] = {
        "lang": lang,
        "category": data.get("category", "default"),
        "category_name": cat_name,
        "points": items,
        "extra_notes": extra_notes,
    }

    check_context_for_internal_fields(context)

    if not COMMS_TEMPLATE_PATH.exists():
        raise InspectionProtocolError(f"Inspection template missing: {COMMS_TEMPLATE_PATH}")

    template_str = COMMS_TEMPLATE_PATH.read_text(encoding="utf-8")
    return render_outgoing_template(template_str, context)


def render_inspection_ui_fragment(
    category: str | None = None,
    lang: str = "ru",
    show_secondary: bool = True,
) -> str:
    """Render UI HTML fragment for checklist display."""
    data = load_inspection_checklist(category)
    items = get_checklist_items(category, lang=lang)

    if not show_secondary:
        for it in items:
            it["secondary_text"] = ""

    cat_name = data.get("category_name_ru" if lang == "ru" else "category_name_cs") or data.get("category", "Осмотр")

    context = {
        "category_name": cat_name,
        "points": items,
    }

    if not WEB_TEMPLATE_PATH.exists():
        raise InspectionProtocolError(f"Web inspection template missing: {WEB_TEMPLATE_PATH}")

    tmpl = Template(WEB_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return str(tmpl.render(context))


def enqueue_inspection_protocol_to_thread(
    session: Session,
    thread_id: str,
    category: str | None = None,
    lang: str = "cs",
    actor: str = "SYSTEM",
) -> tuple[Message, Outbox]:
    """Enqueue inspection protocol message into the thread and outbox."""
    thread = session.get(Thread, thread_id)
    if not thread:
        raise InspectionProtocolError(f"Thread {thread_id} not found")

    text = render_inspection_message(category=category, lang=lang)

    msg = Message(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        direction="OUT",
        transport=thread.channel if thread.channel in ("MARKETPLACE", "EMAIL", "TELEGRAM") else "MARKETPLACE",
        text_original=text,
        lang_original=lang,
        sent_at=datetime.now(UTC),
        template_key="inspection_protocol",
        source_evidence={"category": category or "default"},
    )
    session.add(msg)

    outbox_msg = enqueue_outbox(
        session=session,
        transport="EMAIL" if thread.channel == "EMAIL" else "TELEGRAM",
        recipient_hint=thread.counterparty,
        body=text,
        idempotency_key=f"inspection-{thread.id}-{uuid.uuid4().hex[:8]}",
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="INSPECTION_PROTOCOL_ENQUEUED",
        entity="thread",
        entity_id=thread.id,
        data={
            "category": category,
            "lang": lang,
            "message_id": msg.id,
        },
    )
    record_truth(
        session=session,
        claim="INSPECTION_PROTOCOL_ENQUEUED",
        recipient=thread.counterparty,
        evidence={
            "thread_id": thread.id,
            "category": category or "default",
            "message_id": msg.id,
            "points_count": len(get_checklist_items(category, lang=lang)),
        },
    )
    session.flush()
    return msg, outbox_msg
