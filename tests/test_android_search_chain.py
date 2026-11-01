"""Chain test: /api/oc/search → broker (fake) → parsed results (Stage 3).

Verifies the full chain: API endpoint → PhoneBrokerTransport → fake broker
→ results parser → JSON response.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from tests.conftest import clean_database

from nextlife.api.main import create_app
from nextlife.storage.db import get_db_session

FIX = Path(__file__).parent / "fixtures" / "android"
SEARCH_XML = FIX.joinpath("vinted_search_results.xml").read_text(encoding="utf-8")
HOME_XML = FIX.joinpath("vinted_home.xml").read_text(encoding="utf-8")
PNG_1X1 = base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
).decode()


class FakeBrokerState:
    def __init__(self) -> None:
        self.lease_id = "chain-test-lease"
        self.requests: list[dict[str, Any]] = []
        self._dump_count = 0


class _FakeBrokerHandler(BaseHTTPRequestHandler):
    state: FakeBrokerState

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _respond(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()
        self.state.requests.append({"method": "POST", "path": path, "body": body})

        if path == "/lease":
            self._respond(201, {"id": self.state.lease_id, "status": "active"})
        elif path.endswith("/heartbeat"):
            self._respond(200, {"ok": True})
        elif "/ops/" in path:
            op = path.rsplit("/", 1)[-1]
            if op == "ui_dump":
                self.state._dump_count += 1
                # First dump = home screen, subsequent = search results
                xml = HOME_XML if self.state._dump_count <= 1 else SEARCH_XML
                self._respond(200, {"xml": xml})
            elif op == "screenshot":
                self._respond(200, {"image": PNG_1X1})
            elif op == "launch":
                self._respond(200, {"ok": True})
            elif op == "tap_selector":
                self._respond(200, {"ok": True})
            elif op == "text":
                self._respond(200, {"ok": True})
            elif op == "keyevent":
                self._respond(200, {"ok": True})
            elif op == "wait_for":
                self._respond(200, {"found": True})
            else:
                self._respond(200, {"ok": True})
        else:
            self._respond(404, {"error": "not found"})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        self.state.requests.append({"method": "GET", "path": path, "body": None})
        if path == "/health":
            self._respond(
                200,
                {
                    "phone": "device",
                    "platforms": [
                        {"platform": "vinted", "versionName": "26.39.0", "available": True},
                    ],
                },
            )
        elif path.startswith("/lease/"):
            self._respond(200, {"id": self.state.lease_id, "status": "active"})
        else:
            self._respond(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        self.state.requests.append({"method": "DELETE", "path": path, "body": None})
        if path.startswith("/lease/"):
            self._respond(200, {"ok": True})
        else:
            self._respond(404, {"error": "not found"})


@pytest.fixture()
def fake_broker():
    state = FakeBrokerState()
    server = HTTPServer(("127.0.0.1", 0), _FakeBrokerHandler)
    _FakeBrokerHandler.state = state
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"state": state, "port": port, "base_url": f"http://127.0.0.1:{port}"}
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def oc_headers() -> dict[str, str]:
    import os

    return {"Authorization": f"Bearer {os.environ['NEXTLIFE_OPENCLAW_TOKEN']}"}


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    with get_db_session() as session:
        clean_database(session)
    yield


def test_search_endpoint_on_fake_broker(client, oc_headers, fake_broker, monkeypatch) -> None:
    """Full chain: POST /api/oc/search → fake broker → parsed JSON results."""
    import os

    from nextlife.android.transport import PhoneBrokerTransport

    # Patch make_phone_broker_transport to point at the fake broker
    def fake_factory(config):
        return PhoneBrokerTransport(
            base_url=fake_broker["base_url"],
            token="test-token",
            lease_max_duration_s=300,
            heartbeat_interval_s=999.0,  # no heartbeats during test
        )

    monkeypatch.setattr(
        "nextlife.android.transport.make_phone_broker_transport",
        fake_factory,
    )

    resp = client.post(
        "/api/oc/search",
        json={"platform": "vinted", "query": "iphone", "limit": 5},
        headers=oc_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["platform"] == "vinted"
    assert data["query"] == "iphone"
    assert data["app_version"] == "26.39.0"
    assert data["count"] >= 1
    assert len(data["results"]) >= 1
    # First result should be the iPhone 12 Pro card
    assert "Iphone" in data["results"][0]["title"] or "iPhone" in data["results"][0]["title"]
    assert data["results"][0]["currency"] == "Kč"
    # Verify the broker received the lease + ops
    ops = [r["path"] for r in fake_broker["state"].requests if "ops/" in r.get("path", "")]
    assert any("ui_dump" in op for op in ops)
    assert any("tap_selector" in op for op in ops)
    assert any("text" in op for op in ops)
    assert any("keyevent" in op for op in ops)


def test_search_endpoint_single_lease(client, oc_headers, fake_broker, monkeypatch) -> None:
    """One nextlife search call = exactly one POST /lease (owner requirement)."""
    from nextlife.android.transport import PhoneBrokerTransport

    def fake_factory(config):
        return PhoneBrokerTransport(
            base_url=fake_broker["base_url"],
            token="test-token",
            lease_max_duration_s=300,
            heartbeat_interval_s=999.0,
        )

    monkeypatch.setattr(
        "nextlife.android.transport.make_phone_broker_transport",
        fake_factory,
    )

    resp = client.post(
        "/api/oc/search",
        json={"platform": "vinted", "query": "iphone", "limit": 5},
        headers=oc_headers,
    )
    assert resp.status_code == 200, resp.text
    # Exactly one POST /lease
    lease_posts = [r for r in fake_broker["state"].requests if r["path"] == "/lease" and r["method"] == "POST"]
    assert len(lease_posts) == 1, f"Expected 1 lease, got {len(lease_posts)}"
    # Exactly one DELETE /lease
    lease_deletes = [r for r in fake_broker["state"].requests if r["path"].startswith("/lease/") and r["method"] == "DELETE"]
    assert len(lease_deletes) == 1, f"Expected 1 lease release, got {len(lease_deletes)}"


def test_search_endpoint_unknown_platform(client, oc_headers) -> None:
    """A platform without an ANDROID config returns 400."""
    resp = client.post(
        "/api/oc/search",
        json={"platform": "nonexistent", "query": "test", "limit": 5},
        headers=oc_headers,
    )
    assert resp.status_code == 400
    assert "no ANDROID channel" in resp.json()["detail"]


def test_search_endpoint_auth_required(client) -> None:
    """Without the oc token, the endpoint returns 401."""
    resp = client.post(
        "/api/oc/search",
        json={"platform": "vinted", "query": "test", "limit": 5},
    )
    assert resp.status_code == 401
