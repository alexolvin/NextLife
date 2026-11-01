
import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import (
    BaseConnector,
    CapabilityNotSupported,
    RawListing,
    UnsafeUrlNavigationError,
)
from nextlife.core.config import MarketplaceConfig


class DummyConnector(BaseConnector):
    def search(self, query: str) -> list[RawListing]:
        return []


@pytest.fixture
def base_config():
    return MarketplaceConfig(
        key="test_cz",
        title="Test Marketplace",
        country="CZ",
        currency="CZK",
        access_mode="DIRECT",
        base_url="https://test.cz",
        capabilities={
            "search": True,
            "post": True,
            "edit": False,  # Note edit is disabled
            "archive": True,
            "messaging": True,
        },
        selectors={
            "search_result": ".item",
            "price": ".price",
        },
        auth={"type": "none", "secret_ref": ""},
        mapping={"category_map": {}, "condition_map": {}},
        rate_limits={"requests_per_minute": 30, "min_delay_seconds": 1},
    )


def test_capability_check_and_not_supported(base_config):
    conn = DummyConnector(base_config)
    # Supported
    conn.check_capability("post")
    conn.check_capability("search")

    # Disabled capability
    with pytest.raises(CapabilityNotSupported, match="does not support 'edit' capability"):
        conn.update("123", {"title": "new title"})


def test_publish_enforces_internal_field_filter(base_config):
    conn = DummyConnector(base_config)
    # Valid publish
    res = conn.publish({"title": "PlayStation 5", "price": 10000})
    assert "test_cz" in res.external_id
    assert "https://test.cz" in res.external_url

    # Leak attempt
    with pytest.raises(InternalFieldLeakageError):
        conn.publish({"title": "PS5", "price_floor": 8000})


def test_send_message_enforces_outgoing_filter(base_config):
    conn = DummyConnector(base_config)
    assert conn.send_message("thread_1", "Dobrý den, balík odesílám zítra.") is True

    # Leaking price_floor in text
    with pytest.raises(InternalFieldLeakageError):
        conn.send_message("thread_1", "Moje price_floor je 5000 Kč")


def test_assert_safe_url_blocks_untrusted_schemes():
    # Safe urls
    BaseConnector.assert_safe_url("https://www.bazos.cz/inzerat/12345")
    BaseConnector.assert_safe_url("https://sbazar.cz")

    # Untrusted/phishing patterns
    with pytest.raises(UnsafeUrlNavigationError):
        BaseConnector.assert_safe_url("https://zasilkovna-platba.xyz/pay/123")

    with pytest.raises(UnsafeUrlNavigationError):
        BaseConnector.assert_safe_url("https://t.me/scammer_link")
