"""Buyer proxy communication service without binding commitments (TZ v2.0 Section I.3 / Task T-073).

DoD:
- Contact with external seller strictly without obligations or commitments.
- Report delivered to owner.
- Prohibition of agreeing on price, reserving, or promising prepayment enforced by forbidden phrases validator.
- Checked by outgoing filter.
- Module length <= 400 lines.
"""

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.comms.outbox import enqueue_outbox
from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import BuyCandidate, BuyRequest

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
COMMS_DIR = BASE_DIR / "templates" / "comms"
INQUIRY_TEMPLATE = COMMS_DIR / "buyer_non_binding_inquiry.jinja2"
OWNER_REPORT_TEMPLATE = COMMS_DIR / "buyer_proxy_owner_report.jinja2"

FORBIDDEN_COMMITMENT_EXPRESSIONS = [
    # Russian
    "покупаю",
    "беру",
    "согласен на цену",
    "согласен купить",
    "забронируйте",
    "бронирую",
    "переведу предоплату",
    "отправлю аванс",
    "гарантирую покупку",
    "готов перевести деньги",
    "считайте проданным",
    # Czech
    "kupuji",
    "beru",
    "souhlasím s cenou",
    "rezervuji",
    "zarezervujte",
    "pošlu zálohu",
    "zaplatím předem",
    "garantuji koupi",
    "pošlu peníze hned",
    "beru za tuto cenu",
    "závazně objednávám",
    # English
    "i will buy",
    "i agree to the price",
    "reserve for me",
    "i will prepay",
    "sending deposit",
    "guarantee purchase",
]


class BindingCommitmentProhibitedError(NextLifeError):
    """Raised when an outgoing inquiry makes a binding price, reservation, or payment commitment."""


class BuyerCommunicationError(NextLifeError):
    """Raised when communication operation fails."""


def validate_non_binding_inquiry(text: str) -> None:
    """Validate that message makes zero price, booking, or prepayment commitments."""
    lower = text.lower()
    for phrase in FORBIDDEN_COMMITMENT_EXPRESSIONS:
        if phrase in lower:
            raise BindingCommitmentProhibitedError(
                f"Forbidden commitment expression detected in inquiry: '{phrase}'. "
                "Agent is prohibited from agreeing to prices, booking, or promising prepayments."
            )


def render_non_binding_inquiry(
    query: str,
    questions: list[str],
    lang: str = "cs",
) -> str:
    """Render non-binding seller inquiry text."""
    context = {
        "query": query,
        "questions": questions,
        "lang": lang,
    }
    check_context_for_internal_fields(context)

    if not INQUIRY_TEMPLATE.exists():
        raise BuyerCommunicationError(f"Inquiry template missing: {INQUIRY_TEMPLATE}")

    template_str = INQUIRY_TEMPLATE.read_text(encoding="utf-8")
    rendered = render_outgoing_template(template_str, context)

    # Validate against forbidden commitments
    validate_non_binding_inquiry(rendered)
    return rendered


def render_buyer_proxy_owner_report(
    query: str,
    marketplace_key: str,
    external_url: str,
    price: str,
    currency: str,
    questions: list[str],
) -> str:
    """Render owner status report regarding seller contact attempt."""
    context = {
        "query": query,
        "marketplace_key": marketplace_key,
        "external_url": external_url,
        "price": price,
        "currency": currency,
        "questions": questions,
    }
    check_context_for_internal_fields(context)

    if not OWNER_REPORT_TEMPLATE.exists():
        raise BuyerCommunicationError(f"Owner report template missing: {OWNER_REPORT_TEMPLATE}")

    template_str = OWNER_REPORT_TEMPLATE.read_text(encoding="utf-8")
    return render_outgoing_template(template_str, context)


def send_seller_inquiry(
    session: Session,
    candidate_id: str,
    questions: list[str],
    lang: str = "cs",
    actor: str = "AGENT",
) -> dict[str, Any]:
    """Execute contact with seller on behalf of owner without binding commitments.

    DoD:
    - Verifies zero price agreement, no reservation, no prepayment promise.
    - Sets candidate.contacted = True.
    - Sends report to owner.
    """
    cand = session.get(BuyCandidate, candidate_id)
    if not cand:
        raise BuyerCommunicationError(f"BuyCandidate '{candidate_id}' not found")

    req = session.get(BuyRequest, cand.request_id) if cand.request_id else None
    query_title = req.query if req else "Poptávané zboží"

    # Render and strictly validate inquiry text
    inquiry_text = render_non_binding_inquiry(
        query=query_title,
        questions=questions,
        lang=lang,
    )

    # Update candidate state
    cand.contacted = True

    # Enqueue seller inquiry message
    seller_outbox = enqueue_outbox(
        session=session,
        transport="EMAIL" if cand.marketplace_key == "bazos_cz" else "TELEGRAM",
        recipient_hint=cand.external_url,
        body=inquiry_text,
        idempotency_key=f"seller-inq-{cand.id}-{uuid.uuid4().hex[:6]}",
    )

    # Render report for the owner
    owner_report_text = render_buyer_proxy_owner_report(
        query=query_title,
        marketplace_key=cand.marketplace_key,
        external_url=cand.external_url,
        price=str(cand.price),
        currency=cand.currency,
        questions=questions,
    )

    owner_outbox = enqueue_outbox(
        session=session,
        transport="TELEGRAM",
        recipient_hint="OWNER",
        body=owner_report_text,
        idempotency_key=f"buyer-rep-{cand.id}-{uuid.uuid4().hex[:6]}",
    )

    record_audit_event(
        session=session,
        actor=actor,
        action="BUYER_SELLER_INQUIRY_SENT",
        entity="buy_candidate",
        entity_id=cand.id,
        data={
            "questions_count": len(questions),
            "external_url": cand.external_url,
            "non_binding": True,
        },
    )

    record_truth(
        session=session,
        claim="BUYER_SELLER_INQUIRY_DISPATCHED",
        recipient="OWNER",
        evidence={
            "candidate_id": cand.id,
            "external_url": cand.external_url,
            "questions": questions,
            "non_binding": True,
        },
    )
    session.flush()

    return {
        "candidate_id": cand.id,
        "contacted": True,
        "seller_outbox_id": seller_outbox.id,
        "owner_outbox_id": owner_outbox.id,
        "inquiry_text": inquiry_text,
    }
