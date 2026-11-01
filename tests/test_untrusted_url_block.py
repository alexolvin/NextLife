"""Tests for untrusted message URL blocking in browser automation layer (T-053, Section III.9).

DoD:
- Technical prohibition: opening links from counterparty messages is strictly blocked.
- Any attempt to pass a URL from an incoming message into the browser automation layer raises an exception.
- Legitimate system navigation to whitelisted domains succeeds.
- Audit event UNSAFE_URL_BLOCKED is recorded.
- Module length <= 400 lines.
"""

import pytest
from sqlalchemy import select

from nextlife.connectors.base import BaseConnector
from nextlife.connectors.browser import (
    BrowserAutomationLayer,
    UntrustedMessageUrlBlockedError,
)
from nextlife.core.config import load_config
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent


def test_passing_url_with_message_source_raises_untrusted_url_error():
    """DoD: Attempting to pass a URL from a message into browser layer raises exception."""
    browser = BrowserAutomationLayer()
    url = "https://dpd-platba-overeni.cz/pay/999"

    with pytest.raises(UntrustedMessageUrlBlockedError) as exc_info:
        browser.navigate(url, source="MESSAGE")

    assert "Security violation" in str(exc_info.value)
    assert "counterparty messages is strictly prohibited" in str(exc_info.value)


def test_navigate_url_from_message_method_strictly_raises_exception():
    browser = BrowserAutomationLayer()
    url = "https://zasilkovna-platba.xyz/login"

    with pytest.raises(UntrustedMessageUrlBlockedError):
        browser.navigate_url_from_message(url)


def test_registered_message_url_blocks_subsequent_navigation_attempt():
    browser = BrowserAutomationLayer()
    incoming_msg = "Dobrý den, posílám odkaz na platbu: https://post-online-overeni.cz/track"

    # Message content extracted and registered
    extracted = browser.register_message_content(incoming_msg)
    assert len(extracted) == 1
    assert "https://post-online-overeni.cz/track" in extracted[0]

    # Any later attempt to navigate to that URL must be rejected regardless of source
    with pytest.raises(UntrustedMessageUrlBlockedError) as exc_info:
        browser.navigate("https://post-online-overeni.cz/track", source="SYSTEM")

    assert "untrusted message" in str(exc_info.value)
    assert "counterparty messages" in str(exc_info.value)


def test_connector_open_in_browser_blocks_message_source():
    cfg = load_config()
    bazos_cfg = cfg.marketplaces["bazos_cz"]

    class MockBazos(BaseConnector):
        def search(self, query):
            return []

    conn = MockBazos(bazos_cfg)

    with pytest.raises(UntrustedMessageUrlBlockedError):
        conn.open_in_browser("https://scam-site.cz/confirm", source="MESSAGE")


def test_legitimate_marketplace_url_succeeds():
    browser = BrowserAutomationLayer()
    res = browser.navigate("https://www.bazos.cz/inzerat/12345", source="SYSTEM")
    assert res["status"] == "NAVIGATED"
    assert res["domain"] == "www.bazos.cz"


def test_blocked_navigation_records_audit_event():
    with get_db_session() as session:
        browser = BrowserAutomationLayer(record_audit=True)
        bad_url = "https://t.me/scammer_crypto_bot"

        with pytest.raises(UntrustedMessageUrlBlockedError):
            browser.navigate(bad_url, source="MESSAGE")

        # Verify audit event in DB
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "UNSAFE_URL_BLOCKED")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.data["url"] == bad_url
        assert "MESSAGE" in audit.data["reason"]
