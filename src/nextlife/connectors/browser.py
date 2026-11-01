"""Browser automation security layer and untrusted message URL blocker (TZ v2.0 Section III.9).

DoD:
- Technical prohibition: opening links from counterparty messages is strictly prohibited.
- Attempting to pass a URL from a message into the browser automation layer raises an exception.
- Whitelist validation for authorized marketplace and system operations.
- Security audit events logged upon blocked navigation attempts.
- Module length <= 400 lines.
"""

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from nextlife.connectors.base import UnsafeUrlNavigationError
from nextlife.obs.audit import record_audit_event
from nextlife.storage.db import get_db_session

URL_PATTERN = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)

DEFAULT_ALLOWED_DOMAINS = {
    "bazos.cz",
    "www.bazos.cz",
    "aukro.cz",
    "www.aukro.cz",
    "sbazar.cz",
    "www.sbazar.cz",
    "ecb.europa.eu",
    "www.ecb.europa.eu",
    "127.0.0.1",
    "localhost",
}


class UntrustedMessageUrlBlockedError(UnsafeUrlNavigationError):
    """Raised when an attempt is made to pass or navigate a URL from a counterparty message."""


class BrowserAutomationLayer:
    """Browser automation controller with strict security enforcement against untrusted message URLs."""

    def __init__(
        self,
        allowed_domains: set[str] | None = None,
        *,
        record_audit: bool = True,
    ) -> None:
        self.allowed_domains = allowed_domains or set(DEFAULT_ALLOWED_DOMAINS)
        self._untrusted_message_urls: set[str] = set()
        self.record_audit = record_audit

    def register_untrusted_url(self, url: str) -> None:
        """Register a URL known to originate from counterparty communications."""
        cleaned = self._clean_url(url)
        if cleaned:
            self._untrusted_message_urls.add(cleaned)

    def register_message_content(self, text: str) -> list[str]:
        """Extract all links from incoming message text and register them as strictly blocked."""
        found = [m.group(0) for m in URL_PATTERN.finditer(text)]
        for link in found:
            self.register_untrusted_url(link)
        return found

    def _clean_url(self, url: str) -> str:
        clean = url.strip().lower()
        if clean.endswith("/") or clean.endswith("."):
            clean = clean[:-1]
        return clean

    def _extract_domain(self, url: str) -> str:
        proto = "http"
        parsed = urlparse(url if "://" in url else f"{proto}://{url}")
        return (parsed.hostname or "").lower()

    def navigate(
        self,
        url: str,
        *,
        source: str = "SYSTEM",
        actor: str = "AGENT",
    ) -> dict[str, Any]:
        """Navigate to a URL using browser automation.

        DoD:
        - Passing URL from message (source='MESSAGE' or flagged URL) strictly raises UntrustedMessageUrlBlockedError.
        - Non-whitelisted or suspicious external patterns are rejected.
        """
        cleaned_url = self._clean_url(url)

        # 1. Check explicit source flag
        if source.upper() in ("MESSAGE", "INCOMING_MESSAGE", "COUNTERPARTY", "BUYER", "CHAT"):
            self._record_blocked(cleaned_url, "EXPLICIT_MESSAGE_SOURCE", actor)
            raise UntrustedMessageUrlBlockedError(
                f"Security violation: automated browser navigation to URL '{url}' originated from an incoming message. "
                "Opening links from counterparty messages is strictly prohibited (TZ Section III.9)."
            )

        # 2. Check untrusted registered message URLs
        if cleaned_url in self._untrusted_message_urls:
            self._record_blocked(cleaned_url, "REGISTERED_UNTRUSTED_MESSAGE_URL", actor)
            raise UntrustedMessageUrlBlockedError(
                f"Security violation: URL '{url}' is registered as originating from an untrusted message. "
                "Opening links from counterparty messages is strictly prohibited (TZ Section III.9)."
            )

        # 3. Check phishing / scam regex patterns
        forbidden_patterns = [
            r"t\.me/",
            r"wa\.me/",
            r"api\.whatsapp\.com",
            r"platba",
            r"zasilkovna-platba",
            r"dpd-overeni",
            r"balikovna-potvrzeni",
        ]
        for pat in forbidden_patterns:
            if re.search(pat, cleaned_url):
                self._record_blocked(cleaned_url, f"FORBIDDEN_PATTERN_{pat}", actor)
                raise UntrustedMessageUrlBlockedError(
                    f"Security violation: URL '{url}' matches forbidden scam/phishing pattern '{pat}'."
                )

        # 4. Domain whitelist check
        domain = self._extract_domain(cleaned_url)
        if domain not in self.allowed_domains:
            self._record_blocked(cleaned_url, "DOMAIN_NOT_WHITELISTED", actor)
            raise UntrustedMessageUrlBlockedError(
                f"Security violation: domain '{domain}' for URL '{url}' is not in authorized system whitelist."
            )

        # Authorized navigation proceeds
        return {
            "status": "NAVIGATED",
            "url": url,
            "domain": domain,
            "source": source,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def navigate_url_from_message(self, url: str) -> None:
        """Convenience method directly attempting navigation with message source origin."""
        self.navigate(url, source="MESSAGE")

    def _record_blocked(self, url: str, reason: str, actor: str) -> None:
        if not self.record_audit:
            return
        try:
            with get_db_session() as session:
                record_audit_event(
                    session=session,
                    actor=actor,
                    action="UNSAFE_URL_BLOCKED",
                    entity="url",
                    entity_id=url[:100],
                    data={"url": url, "reason": reason},
                )
                session.commit()
        except Exception:
            pass


# Global singleton instance for system usage
browser_automation = BrowserAutomationLayer()
