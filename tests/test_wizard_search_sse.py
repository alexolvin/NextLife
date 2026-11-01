"""Wizard step 1 SSE search: a hanging marketplace must not block the 'done' event.

Regression: vinted (phone broker) can hang ~88s when the phone is unavailable,
which used to block the whole search. The stream now enforces a deadline and
reports the hanging marketplace as an honest timeout.
"""
import time

from fastapi.testclient import TestClient

from nextlife.api import routes_lots
from nextlife.api.main import create_app
from nextlife.api.routes import check_auth


class _HangingConnector:
    def search(self, q):
        time.sleep(10)
        return []


class _FastConnector:
    def search(self, q):
        return []


def _mock_get_connector(mp_key, cfg):
    return _HangingConnector() if mp_key == "bazos_cz" else _FastConnector()


def _mock_vinted(q, cfg, limit, *args, **kwargs):
    time.sleep(10)
    return []


def test_sse_search_timeout_does_not_block_done(monkeypatch):
    monkeypatch.setattr(routes_lots, "WIZARD_SEARCH_TIMEOUT_S", 2.0)
    monkeypatch.setattr("nextlife.connectors.registry.get_connector", _mock_get_connector)
    monkeypatch.setattr("nextlife.api.routes_openclaw._search_vinted_android", _mock_vinted)

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    t0 = time.monotonic()
    resp = client.post(
        "/ui/lot-wizard/search",
        data={"query": "test"},
        headers={"Accept": "text/event-stream"},
    )
    elapsed = time.monotonic() - t0

    assert resp.status_code == 200
    assert "event: done" in resp.text
    # Hanging marketplaces are reported as honest timeouts.
    assert "bazos_cz" in resp.text and "Timeout" in resp.text
    assert "vinted" in resp.text and "Timeout" in resp.text
    # The stream is bounded by the 2s deadline, not the 10s hang.
    assert elapsed < 8


def _mock_vinted_progress(q, cfg, limit, *args, **kwargs):
    """Vinted that reports per-card progress via the on_progress callback (5th arg)."""
    on_progress = args[1] if len(args) > 1 else kwargs.get("on_progress")
    if on_progress is not None:
        for n in (1, 2, 3):
            on_progress(n)
    return []


def test_sse_search_streams_vinted_progress(monkeypatch):
    """The Vinted share-flow progress (cards collected) is streamed as 'progress' events."""
    monkeypatch.setattr("nextlife.connectors.registry.get_connector", _mock_get_connector)
    monkeypatch.setattr(
        "nextlife.api.routes_openclaw._search_vinted_android", _mock_vinted_progress
    )

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    resp = client.post(
        "/ui/lot-wizard/search",
        data={"query": "rtx 3090"},
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200
    # Per-card progress events for vinted.
    assert "event: progress" in resp.text
    assert '"marketplace": "vinted"' in resp.text
    assert '"cards": 3' in resp.text
    assert "event: done" in resp.text
