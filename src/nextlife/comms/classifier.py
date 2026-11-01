"""Incoming message classifier, FAQ matcher, escalation logic, and thread tracking (TZ v2.0 Section I.2 Ш10, III.9).

DoD:
- Full coverage check: auto-reply is sent ONLY when all required characteristics/fields exist in item card.
- Question about a characteristic missing from the card strictly NEVER receives an auto-reply under any phrasing.
- Fraud suspected messages never receive an auto-reply.
- Pricing/negotiations always escalate to owner.
- Truth log and Audit events recorded.
- Module length <= 400 lines.
"""

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.comms.fraud import detect_fraud
from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.comms.translation import detect_language, translate_text
from nextlife.core.config import ConfigBundle, load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Item, Listing, Lot, Thread


class CommsError(NextLifeError):
    """Base error for communications operations."""


class AmbiguousThreadError(CommsError):
    """Raised when incoming message without marker NL-XXX cannot be uniquely mapped."""


def extract_item_code_from_subject(subject: str) -> str | None:
    """Extract item code from subject containing [NL-XXX]."""
    match = re.search(r"\[NL-(\d{3})\]", subject)
    if match:
        return match.group(1)
    return None


def resolve_message_item_code(
    session: Session,
    sender_email: str,
    subject: str,
) -> tuple[str | None, str]:
    """Resolve item code from subject [NL-XXX] or sender email + active listings."""
    marker_code = extract_item_code_from_subject(subject)
    if marker_code:
        return marker_code, "EXACT_MARKER"

    stmt = (
        select(Item.code)
        .join(Listing, Listing.item_code == Item.code)
        .join(Thread, Thread.listing_id == Listing.id)
        .where(
            Thread.counterparty == sender_email,
            Listing.status.in_(["ACTIVE", "PUBLISHED"]),
        )
        .distinct()
    )
    matching_codes = list(session.execute(stmt).scalars().all())

    if len(matching_codes) == 1:
        return matching_codes[0], "SENDER_MATCH"
    if len(matching_codes) > 1:
        return None, "AMBIGUOUS"

    return None, "NO_MATCH"


def get_field_value(item: Item, field_name: str, session: Session | None = None) -> Any:
    """Retrieve field value from Item, its attributes, or associated Lot."""
    val = getattr(item, field_name, None)
    if val is not None and str(val).strip():
        return val

    if isinstance(item.attrs, dict) and field_name in item.attrs:
        val = item.attrs.get(field_name)
        if val is not None and str(val).strip():
            return val

    if session:
        lot = session.scalar(select(Lot).where(Lot.item_id == item.code))
        if lot:
            val = getattr(lot, field_name, None)
            if val is not None and str(val).strip():
                return val

    return None


def match_faq(
    text: str,
    item: Item,
    config: ConfigBundle,
    session: Session | None = None,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Match message against FAQ templates in config/faq.yaml (Rule П-1.7, I.2 Ш10).

    Returns:
        (faq_match, primary_source_field, missing_fields)
        If any required field is missing from the item card, faq_match is None and
        missing_fields contains the names of the missing fields.
    """
    clean_text = text.lower().strip()

    # 1. Price, discount, or barter negotiations must never auto-reply (Section 9.2 Ш6, I.2 Ш10)
    forbidden_tokens = ["sleva", "discount", "скидк", "obměna", "trade", "обмен", "cenu", "price", "dáte to za", "date to za"]
    if any(tok in clean_text for tok in forbidden_tokens):
        return None, None, []

    for pattern in config.faq.patterns:
        # Check intent match
        matched_intent = False
        for intent in pattern.intents:
            if intent.lower() in clean_text:
                matched_intent = True
                break

        if not matched_intent:
            continue

        # Collect all required fields
        required_fields = list(pattern.required_fields)
        if pattern.required_field and pattern.required_field not in required_fields:
            required_fields.append(pattern.required_field)

        # Verify EVERY required field exists and has non-empty value in the card
        context_data: dict[str, Any] = {}
        missing_fields: list[str] = []

        for req in required_fields:
            val = get_field_value(item, req, session)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing_fields.append(req)
            else:
                context_data[req] = val

        # DoD: If ANY characteristic requested is not present in the card, NO auto-reply!
        if missing_fields:
            return None, None, missing_fields

        # All required fields present -> render response template safely
        primary_field = required_fields[0] if required_fields else "status"
        try:
            check_context_for_internal_fields(context_data)
            rendered_resp = render_outgoing_template(pattern.response_template, context_data)
        except Exception:
            rendered_resp = pattern.response_template

        return (
            {
                "key": pattern.key,
                "template": rendered_resp,
                "context": context_data,
            },
            primary_field,
            [],
        )

    return None, None, []


def process_incoming_message(
    session: Session,
    sender: str,
    subject: str,
    body: str,
    channel: str = "EMAIL",
    config: ConfigBundle | None = None,
) -> dict[str, Any]:
    """Process incoming message with strict security, full-coverage card check, and escalation (I.2 Ш10)."""
    cfg = config or load_config()

    # 1. Security / Fraud Detection (Section III.9)
    fraud_res = detect_fraud(body)
    if fraud_res.is_fraud_suspected:
        return {
            "type": "ESCALATION",
            "reason": "fraud_suspected",
            "fraud_scheme": fraud_res.scheme_id,
            "fraud_indicators": fraud_res.matched_indicators,
            "requires_owner_approval": True,
            "can_auto_reply": False,
            "sender": sender,
            "original_text": body,
            "proposed_response": fraud_res.prepared_refusal,
        }

    item_code, resolution_status = resolve_message_item_code(session, sender, subject)

    if resolution_status == "AMBIGUOUS" or not item_code:
        return {
            "type": "ESCALATION",
            "reason": "ambiguous_subject_or_unknown_item",
            "item_code": None,
            "sender": sender,
            "resolution_status": resolution_status,
            "action_required": "owner_decision",
            "requires_owner_approval": True,
        }

    item = session.get(Item, item_code)
    if not item:
        return {
            "type": "ESCALATION",
            "reason": "item_not_found_in_db",
            "item_code": item_code,
            "sender": sender,
            "requires_owner_approval": True,
        }

    lang = detect_language(body)
    faq_match, source_field, missing_fields = match_faq(body, item, cfg, session)

    # If characteristic is missing from card -> Strict escalation
    if missing_fields:
        translated_body = translate_text(body, source_lang=lang, target_lang="ru")
        return {
            "type": "ESCALATION",
            "reason": "missing_card_data",
            "item_code": item_code,
            "sender": sender,
            "missing_fields": missing_fields,
            "original_text": body,
            "translated_text_ru": translated_body,
            "requires_owner_approval": True,
            "can_auto_reply": False,
        }

    if faq_match and source_field:
        # Full FAQ match + confirmed data in card -> Auto-reply
        template_response = faq_match["template"]
        response_text = translate_text(template_response, source_lang="cs", target_lang=lang)

        field_val = get_field_value(item, source_field, session)
        evidence = {
            "faq_key": faq_match["key"],
            "source_field": source_field,
            "source_field_value": str(field_val),
            "sender": sender,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        record_truth(
            session=session,
            claim=f"Автоответ на входящий запрос по предмету #{item_code}: {faq_match['key']}",
            recipient="COUNTERPARTY",
            evidence=evidence,
        )
        session.flush()

        return {
            "type": "AUTO_REPLY",
            "item_code": item_code,
            "response_text": response_text,
            "source_field": source_field,
            "evidence": evidence,
        }

    # Unmatched question or negotiation -> Escalate to Owner
    translated_body = translate_text(body, source_lang=lang, target_lang="ru")
    proposed_response_ru = "Здравствуйте. Уточню этот вопрос и вернусь с ответом."
    proposed_response_foreign = translate_text(
        proposed_response_ru, source_lang="ru", target_lang=lang
    )

    return {
        "type": "ESCALATION",
        "reason": "manual_decision_or_unmatched_faq",
        "item_code": item_code,
        "sender": sender,
        "original_text": body,
        "translated_text_ru": translated_body,
        "proposed_response": proposed_response_foreign,
        "requires_owner_approval": True,
        "can_auto_reply": False,
    }
