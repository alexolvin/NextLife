"""Tests for PhoneBrokerTransport against a fake HTTP broker (local server).

Covers Phase 0 items 1, 2, 3, 5, 7 which FakeTransport does not verify:
- 1: token via resolve_secret; missing token -> clear error
- 2: heartbeat with interval from config -> /lease/{id}/heartbeat
- 3: priority per operation in POST /lease body
- 5: text passes Unicode without distortion; wait_for sends selector+timeout
- 7: recognizer selects fingerprints by marketplace
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from nextlife.android.screens import make_recognizer
from nextlife.android.transport import PhoneBrokerTransport, make_phone_broker_transport
from nextlife.android.errors import BridgeTransportMissingError
from nextlife.core.config import (
    AndroidMarketplaceConfig,
    PhoneBrokerConfig,
    ScreenFingerprintConfig,
)

# 1x1 black pixel PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# Minimal valid ui_dump XML
DUMP_XML = '<?xml version="1.0"?><hierarchy><node resource-id="target"/></hierarchy>'


class FakeBrokerState:
    """Records all requests from the transport."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.heartbeat_count = 0
        self.wait_for_count = 0
        # Control for wait_for: how many ui_dump calls before the selector appears
        self.wait_for_appear_after = 0
        self.lease_id = "test-lease-001"
        # XML returned by the ui_dump op (empty -> the default DUMP_XML).
        self.ui_dump_xml = ""


class _Handler(BaseHTTPRequestHandler):
    state: FakeBrokerState

    def log_message(self, format: str, *args: Any) -> None:
        pass  # silence

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()
        self.state.requests.append({"method": "POST", "path": path, "body": body})

        if path == "/lease":
            self._respond(201, {"id": self.state.lease_id, "status": "active"})
        elif path.endswith("/heartbeat"):
            self.state.heartbeat_count += 1
            self._respond(200, {"ok": True})
        elif "/ops/" in path:
            op = path.rsplit("/", 1)[-1]
            if op == "ui_dump":
                self._respond(200, {"xml": self.state.ui_dump_xml or DUMP_XML})
            elif op == "wait_for":
                self.state.wait_for_count += 1
                self._respond(200, {"found": True})
            else:
                self._respond(200, {"ok": True})
        else:
            self._respond(404, {"error": "not found"})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        self.state.requests.append({"method": "GET", "path": path, "body": None})

        if path == "/health":
            self._respond(200, {
                "phone": "device",
                "platforms": [{"platform": "vinted", "versionName": "2.0.0"}],
            })
        elif path.startswith("/lease/"):
            self._respond(200, {"id": self.state.lease_id, "status": "active"})
        else:
            self._respond(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        self.state.requests.append({"method": "DELETE", "path": path, "body": None})
        self._respond(200, {"ok": True})

    def _read_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw.decode(errors="replace")}

    def _respond(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def broker_state() -> FakeBrokerState:
    return FakeBrokerState()


@pytest.fixture
def broker_server(broker_state: FakeBrokerState) -> tuple[str, threading.Thread]:
    _Handler.state = broker_state
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", server
    server.shutdown()


def make_transport(broker_server: tuple[str, threading.Thread], **kwargs: Any) -> PhoneBrokerTransport:
    base_url = broker_server[0]
    return PhoneBrokerTransport(
        base_url=base_url,
        token="test-token",
        **kwargs,
    )


# --------------------------------------------------------------------- #
# Item 1: token via resolve_secret
# --------------------------------------------------------------------- #


def test_token_via_resolve_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_phone_broker_transport resolves the token via resolve_secret."""
    monkeypatch.setenv("TEST_BROKER_TOKEN", "resolved-token-value")
    cfg = PhoneBrokerConfig(
        base_url="http://127.0.0.1:9999",
        token_secret_ref="TEST_BROKER_TOKEN",
    )
    t = make_phone_broker_transport(cfg)
    assert t.token == "resolved-token-value"


def test_token_missing_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing token secret -> BridgeTransportMissingError with the ref name."""
    monkeypatch.delenv("TEST_BROKER_TOKEN_MISSING", raising=False)
    cfg = PhoneBrokerConfig(
        base_url="http://127.0.0.1:9999",
        token_secret_ref="TEST_BROKER_TOKEN_MISSING",
    )
    with pytest.raises(BridgeTransportMissingError, match="TEST_BROKER_TOKEN_MISSING"):
        make_phone_broker_transport(cfg)


# --------------------------------------------------------------------- #
# Item 2: heartbeat with interval from config
# --------------------------------------------------------------------- #


def test_heartbeat_sends_requests(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """begin() starts heartbeats at the configured interval."""
    import time

    t = make_transport(broker_server, heartbeat_interval_s=0.1)
    t.begin("vinted", "background")
    try:
        time.sleep(0.35)  # ~3 heartbeats at 0.1s
    finally:
        t.end()
    assert broker_state.heartbeat_count >= 2, (
        f"expected >=2 heartbeats, got {broker_state.heartbeat_count}"
    )
    # Verify heartbeat requests went to /lease/{id}/heartbeat
    hb_requests = [r for r in broker_state.requests if r["path"].endswith("/heartbeat")]
    assert len(hb_requests) >= 2
    assert all(r["path"] == f"/lease/{broker_state.lease_id}/heartbeat" for r in hb_requests)


# --------------------------------------------------------------------- #
# Item 3: priority per operation in POST /lease body
# --------------------------------------------------------------------- #


def test_priority_background_in_lease(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """begin(platform, 'background') sends priority=background in POST /lease."""
    t = make_transport(broker_server)
    t.begin("vinted", "background")
    t.end()
    lease_requests = [r for r in broker_state.requests if r["path"] == "/lease"]
    assert len(lease_requests) == 1
    assert lease_requests[0]["body"]["priority"] == "background"
    assert lease_requests[0]["body"]["platform"] == "vinted"


def test_priority_interactive_in_lease(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """begin(platform, 'interactive') sends priority=interactive in POST /lease."""
    t = make_transport(broker_server)
    t.begin("vinted", "interactive")
    t.end()
    lease_requests = [r for r in broker_state.requests if r["path"] == "/lease"]
    assert len(lease_requests) == 1
    assert lease_requests[0]["body"]["priority"] == "interactive"


# --------------------------------------------------------------------- #
# Item 5: text Unicode + wait_for
# --------------------------------------------------------------------- #


def test_text_passes_unicode_without_distortion(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """_text sends the exact Unicode string to the broker."""
    t = make_transport(broker_server)
    t.begin("vinted", "background")
    try:
        t._text("Příliš žluťoučký kůň")
    finally:
        t.end()
    text_requests = [r for r in broker_state.requests if r["path"].endswith("/ops/text")]
    assert len(text_requests) == 1
    assert text_requests[0]["body"]["text"] == "Příliš žluťoučký kůň"


def test_wait_for_sends_selector_and_timeout(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """_wait_for sends the selector and timeout_s to the broker."""
    t = make_transport(broker_server)
    t.begin("vinted", "background")
    try:
        t._wait_for("resource-id=target", timeout_s=5)
    finally:
        t.end()
    wf_requests = [r for r in broker_state.requests if r["path"].endswith("/ops/wait_for")]
    assert len(wf_requests) == 1
    assert wf_requests[0]["body"]["selector"] == "resource-id=target"
    assert wf_requests[0]["body"]["timeout_s"] == 5


# --------------------------------------------------------------------- #
# Item 7: recognizer selects fingerprints by marketplace
# --------------------------------------------------------------------- #


def test_recognizer_selects_fingerprints_by_marketplace() -> None:
    """Two marketplaces with different fingerprints -> different recognizers."""
    from nextlife.android.screens import ScreenFingerprint

    mp_a = AndroidMarketplaceConfig(
        platform_id="vinted",
        fingerprints=[
            ScreenFingerprintConfig(
                name="vinted_home",
                version="2.0.0",
                resource_ids=["navigation_tab_browse"],
                texts=["Domů"],
            ),
        ],
    )
    mp_b = AndroidMarketplaceConfig(
        platform_id="fb",
        fingerprints=[
            ScreenFingerprintConfig(
                name="fb_home",
                version="1.0.0",
                resource_ids=["fb_nav_bar"],
                texts=["Marketplace"],
            ),
        ],
    )

    rec_a = make_recognizer(
        [ScreenFingerprint(name=f.name, resource_ids=tuple(f.resource_ids), texts=tuple(f.texts)) for f in mp_a.fingerprints]
    )
    rec_b = make_recognizer(
        [ScreenFingerprint(name=f.name, resource_ids=tuple(f.resource_ids), texts=tuple(f.texts)) for f in mp_b.fingerprints]
    )

    # Screen with vinted ids -> recognized by rec_a, not rec_b
    vinted_screen = '<hierarchy><node resource-id="navigation_tab_browse" text="Domů"/></hierarchy>'
    fb_screen = '<hierarchy><node resource-id="fb_nav_bar" text="Marketplace"/></hierarchy>'

    assert rec_a.recognize(vinted_screen) == "vinted_home"
    assert rec_a.recognize(fb_screen) is None
    assert rec_b.recognize(fb_screen) == "fb_home"
    assert rec_b.recognize(vinted_screen) is None


# --------------------------------------------------------------------- #
# Item 9: app_version from /health
# --------------------------------------------------------------------- #


def test_app_version_from_health(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """app_version() returns the versionName from /health (cached on begin)."""
    t = make_transport(broker_server)
    t.begin("vinted", "background")
    try:
        assert t.app_version() == "2.0.0"
    finally:
        t.end()


def test_app_version_empty_when_not_in_health(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """app_version() returns empty string when /health has no versionName."""
    # The fake broker returns versionName="2.0.0" for vinted;
    # use a platform not in the health response.
    t = make_transport(broker_server)
    t.begin("unknown_platform", "background")
    try:
        assert t.app_version() == ""
    finally:
        t.end()


# --------------------------------------------------------------------- #
# Consent screen auto-dismiss (search_in_app)
# --------------------------------------------------------------------- #


def _tap_selectors(state: FakeBrokerState) -> list[str]:
    return [
        r["body"]["selector"]
        for r in state.requests
        if r["path"].endswith("/ops/tap_selector")
    ]


def test_search_in_app_dismisses_consent_when_present(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """Consent screen present -> tapped once before the search field."""
    broker_state.ui_dump_xml = (
        '<?xml version="1.0"?><hierarchy>'
        '<node text="Přijmout vše"/>'
        '<node resource-id="input_search"/>'
        "</hierarchy>"
    )
    t = make_transport(broker_server)
    t.begin("vinted", "background")
    try:
        t.search_in_app("rtx 3090", {
            "search_field": "input_search",
            "consent_selector": "text=Přijmout vše",
        })
    finally:
        t.end()
    selectors = _tap_selectors(broker_state)
    assert "text=Přijmout vše" in selectors
    assert selectors.index("text=Přijmout vše") < selectors.index("resource-id=input_search")


def test_search_in_app_no_consent_tap_when_absent(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """Consent screen absent -> no consent tap, search field tapped as usual."""
    broker_state.ui_dump_xml = (
        '<?xml version="1.0"?><hierarchy>'
        '<node resource-id="input_search"/>'
        "</hierarchy>"
    )
    t = make_transport(broker_server)
    t.begin("vinted", "background")
    try:
        t.search_in_app("rtx 3090", {
            "search_field": "input_search",
            "consent_selector": "text=Přijmout vše",
        })
    finally:
        t.end()
    selectors = _tap_selectors(broker_state)
    assert "text=Přijmout vše" not in selectors
    assert "resource-id=input_search" in selectors


def test_search_in_app_consent_html_entity_encoded(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """Consent text HTML-entity-encoded in the dump is still detected."""
    broker_state.ui_dump_xml = (
        '<?xml version="1.0"?><hierarchy>'
        '<node text="P&#345;ijmout v&#353;e"/>'
        "</hierarchy>"
    )
    t = make_transport(broker_server)
    t.begin("vinted", "background")
    try:
        t._dismiss_consent("text=Přijmout vše")
    finally:
        t.end()
    assert "text=Přijmout vše" in _tap_selectors(broker_state)


# --------------------------------------------------------------------- #
# Wizard «Прервать»: cancel_event stops the share flow early
# --------------------------------------------------------------------- #


def test_extract_card_links_cancel_event_stops_early(
    broker_server: tuple[str, threading.Thread],
    broker_state: FakeBrokerState,
) -> None:
    """A pre-set cancel_event stops extract_card_links before the first card.

    Returns the partial links (none here) and does not send any tap op, so
    the phone lease is released promptly (wizard «Прервать»)."""
    first_xml = (
        '<?xml version="1.0"?><hierarchy>'
        '<node resource-id="item_box_container" content-desc="card 1"/>'
        '<node resource-id="item_box_container" content-desc="card 2"/>'
        "</hierarchy>"
    )
    t = make_transport(broker_server)
    cancel = threading.Event()
    cancel.set()
    urls, after_xml = t.extract_card_links(
        first_xml,
        {"result_card": "item_box_container", "share_menu": "share_menu", "link_limit": "5"},
        cancel,
    )
    assert urls == []
    assert after_xml == first_xml
    assert _tap_selectors(broker_state) == []
