"""Fraud detection engine and security rules (TZ v2.0 Section III.9).

DoD:
- Detects all 9 starting fraud schemes from config/fraud_patterns.yaml:
  1. payment_link
  2. card_credentials
  3. fake_escrow_service
  4. external_messenger_redirect
  5. overpayment_refund
  6. advance_shipment_claim
  7. payment_screenshot
  8. fake_courier_pickup
  9. urgency_pressure
- Marks message with fraud_suspected flag and matched indicator.
- Strictly prohibits substantive auto-replies to suspected messages (can_auto_reply = False).
- Prepares safe polite refusal for owner approval.
- Module length <= 400 lines.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Message

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "fraud_patterns.yaml"

URL_REGEX = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)


class FraudConfigError(NextLifeError):
    """Raised when fraud pattern configuration is missing or malformed."""


@dataclass
class FraudScheme:
    id: str
    name: str
    description: str
    requires_url: bool
    keywords: list[str] = field(default_factory=list)
    regex_patterns: list[str] = field(default_factory=list)


@dataclass
class FraudDetectionResult:
    is_fraud_suspected: bool
    scheme_id: str | None = None
    scheme_name: str | None = None
    matched_indicators: list[str] = field(default_factory=list)
    prepared_refusal: str | None = None
    can_auto_reply: bool = True
    found_urls: list[str] = field(default_factory=list)


def load_fraud_schemes(config_path: Path | None = None) -> tuple[list[FraudScheme], str, str]:
    """Load fraud patterns and safe refusal templates from configuration."""
    cfg_file = config_path or CONFIG_PATH
    if not cfg_file.exists():
        raise FraudConfigError(f"Fraud patterns configuration not found at {cfg_file}")

    with open(cfg_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_schemes = data.get("schemes", [])
    if not raw_schemes:
        raise FraudConfigError("No schemes configured in fraud_patterns.yaml")

    schemes = [
        FraudScheme(
            id=s["id"],
            name=s.get("name", s["id"]),
            description=s.get("description", ""),
            requires_url=s.get("requires_url", False),
            keywords=[k.lower() for k in s.get("keywords", [])],
            regex_patterns=s.get("regex_patterns", []),
        )
        for s in raw_schemes
    ]

    refusal_cs = data.get(
        "safe_refusal_cs",
        "Dobrý den, komunikuji a obchoduji výhradně přes oficiální systém platformy a běžný bankovní převod.",
    )
    refusal_en = data.get(
        "safe_refusal_en",
        "Hello, I communicate and trade exclusively via the platform and standard bank transfer.",
    )

    return schemes, refusal_cs, refusal_en


def extract_urls(text: str) -> list[str]:
    """Extract all web links from text."""
    return [m.group(0) for m in URL_REGEX.finditer(text)]


def detect_fraud(
    text: str,
    *,
    schemes: list[FraudScheme] | None = None,
    refusal_template: str | None = None,
) -> FraudDetectionResult:
    """Analyze an incoming message text against fraud schemes.

    DoD:
    - Detects any of the 9 schemes.
    - If fraud suspected, can_auto_reply is strictly set to False.
    - Matched indicator and scheme name provided.
    - Prepared safe refusal provided.
    """
    if schemes is None or refusal_template is None:
        loaded_schemes, def_cs, _ = load_fraud_schemes()
        schemes = schemes or loaded_schemes
        refusal_template = refusal_template or def_cs

    lower_text = text.lower()
    urls = extract_urls(text)
    has_url = len(urls) > 0

    for scheme in schemes:
        matched: list[str] = []

        # If scheme strictly requires a URL and text has none, skip
        if scheme.requires_url and not has_url:
            continue

        # Check keyword matches
        for kw in scheme.keywords:
            if kw in lower_text:
                matched.append(f"keyword:{kw}")

        # Check regex matches
        for pat in scheme.regex_patterns:
            try:
                if re.search(pat, text, re.IGNORECASE):
                    matched.append(f"regex:{pat}")
            except re.error:
                continue

        # If matched, we report fraud suspicion immediately
        if matched:
            return FraudDetectionResult(
                is_fraud_suspected=True,
                scheme_id=scheme.id,
                scheme_name=scheme.name,
                matched_indicators=matched,
                prepared_refusal=refusal_template,
                can_auto_reply=False,  # CRITICAL DoD: NO substantive auto-reply!
                found_urls=urls,
            )

    # Security policy (TZ v2.0 Section III.9 & DoD C-46):
    # Any incoming counterparty message with external URLs is untrusted.
    # Substantive auto-reply is strictly suppressed to prevent phishing / redirection.
    if has_url:
        return FraudDetectionResult(
            is_fraud_suspected=True,
            scheme_id="untrusted_link",
            scheme_name="Nedůvěryhodný externí odkaz",
            matched_indicators=[f"url:{u}" for u in urls],
            prepared_refusal=refusal_template,
            can_auto_reply=False,
            found_urls=urls,
        )

    return FraudDetectionResult(
        is_fraud_suspected=False,
        can_auto_reply=True,
        found_urls=urls,
    )


def process_incoming_message_security(
    session: Session,
    message_id: str,
    text: str,
    *,
    thread_id: str | None = None,
    actor: str = "AGENT",
) -> FraudDetectionResult:
    """Process incoming message, tag fraud status, and record audit/truth if suspected."""
    result = detect_fraud(text)

    if result.is_fraud_suspected:
        msg = session.get(Message, message_id)
        if msg:
            ev = dict(msg.source_evidence or {})
            ev["fraud_suspected"] = True
            ev["fraud_scheme"] = result.scheme_id
            ev["fraud_indicators"] = result.matched_indicators
            msg.source_evidence = ev
            msg.escalated = True
            session.flush()

        record_audit_event(
            session=session,
            actor=actor,
            action="FRAUD_SUSPECTED",
            entity="message",
            entity_id=message_id,
            data={
                "scheme_id": result.scheme_id,
                "scheme_name": result.scheme_name,
                "matched_indicators": result.matched_indicators,
                "urls_found": result.found_urls,
                "can_auto_reply": False,
            },
        )

        record_truth(
            session=session,
            claim=f"Входящее сообщение #{message_id} классифицировано как подозрительное: {result.scheme_name} (схема: {result.scheme_id}). Автоответ запрещён.",
            recipient="OWNER",
            evidence={
                "message_id": message_id,
                "scheme_id": result.scheme_id,
                "indicators": result.matched_indicators,
            },
        )

    return result
