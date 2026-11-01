"""Item 3: /api/oc/* namespace — token separation, endpoints, approval gate.

Covers the owner rules (2026-10-04):
- oc token opens ONLY /api/oc/*; web token never opens it; oc token never
  opens /ui or the web /api;
- publish (and deal close with an ACTIVE listing) require a current web-UI
  draft approval — otherwise 409 (three mandatory scenarios);
- draft build returns web_url for the owner.
Rule K-1: module <= 400 lines.
"""

import io
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests.conftest import clean_database

from nextlife.api.main import create_app
from nextlife.core.config import load_config
from nextlife.core.secrets import resolve_secret
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Outbox, ResearchRun


def _make_dummy_jpeg() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (120, 90), color="red")
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def oc_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['NEXTLIFE_OPENCLAW_TOKEN']}"}


@pytest.fixture()
def web_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {resolve_secret('NEXTLIFE_WEB_TOKEN')}"}


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    with get_db_session() as session:
        clean_database(session)
    yield
    # Teardown: later modules (test_outgoing_filter..test_price_history) do not
    # clean the DB, so a leaked API-allocated item (random 3-digit code) could
    # collide with their fixed codes (e.g. '993' in test_price_history).
    with get_db_session() as session:
        clean_database(session)


def _create_item(client: TestClient, oc_headers: dict[str, str], title: str = "Test Item One") -> str:
    resp = client.post(
        "/api/oc/items",
        json={"title": title, "category": "electronics", "price": 1000, "currency": "CZK"},
        headers=oc_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["code"]  # ТЗ «Склад» §5.2.1: full stock card


def _draft_build(client: TestClient, oc_headers: dict[str, str], code: str) -> str:
    resp = client.post(f"/api/oc/items/{code}/draft", headers=oc_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["payload_hash"]


def _approve_web(client: TestClient, web_headers: dict[str, str], code: str, payload_hash: str) -> None:
    resp = client.post(f"/ui/draft/{code}/approve?payload_hash={payload_hash}", headers=web_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"


def _update_draft_web(client: TestClient, web_headers: dict[str, str], code: str, text: str) -> None:
    resp = client.post(
        f"/ui/draft/{code}/update", data={"listing_text": text}, headers=web_headers, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text


# --- Token separation -------------------------------------------------------


def test_oc_auth_missing_token(client: TestClient) -> None:
    assert client.get("/api/oc/status").status_code == 401


def test_oc_auth_wrong_token(client: TestClient) -> None:
    resp = client.get("/api/oc/status", headers={"Authorization": "Bearer wrong_token"})
    assert resp.status_code == 401


def test_oc_auth_web_token_rejected(client: TestClient, web_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/status", headers=web_headers).status_code == 401


def test_oc_auth_query_token_ignored(client: TestClient) -> None:
    web_token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    assert client.get(f"/api/oc/status?token={web_token}").status_code == 401


def test_oc_token_rejected_on_web_api(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/dashboard", headers=oc_headers).status_code == 401


def test_oc_token_rejected_on_ui(client: TestClient, oc_headers: dict[str, str]) -> None:
    resp = client.get("/ui/items", headers={**oc_headers, "Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code in (303, 307)
    assert "/ui/login" in resp.headers["location"]
    resp_json = client.get(
        "/ui/items", headers={**oc_headers, "Accept": "application/json"}, follow_redirects=False
    )
    assert resp_json.status_code == 401


def test_public_endpoints_stay_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/status").status_code == 200


# --- Items ------------------------------------------------------------------


def test_oc_status(client: TestClient, oc_headers: dict[str, str]) -> None:
    data = client.get("/api/oc/status", headers=oc_headers).json()
    for key in ("items_by_status", "jobs_in_queue", "connectors", "android_bridge"):
        assert key in data


def test_oc_item_create_and_show(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_item(client, oc_headers)
    data = client.get(f"/api/oc/items/{code}", headers=oc_headers).json()
    assert data["code"] == code
    assert data["title"] == "Test Item One"
    assert data["status"] == "DRAFT"


def test_oc_item_show_not_found(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/items/ZZZ", headers=oc_headers).status_code == 404


# --- Research (service patched: no live marketplace traffic in tests) -------


def test_oc_research_start_and_report(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_item(client, oc_headers)
    from nextlife import services

    def fake_run_research(session: Any, item_code: str, query: str, config: Any, **kwargs: Any) -> str:
        run_id = str(uuid.uuid4())
        session.add(
            ResearchRun(
                id=run_id,
                item_code=item_code,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                status="FINISHED",
                marketplaces={"queried": []},
                error={},
            )
        )
        session.flush()
        return run_id

    original = services.research.run_research
    services.research.run_research = fake_run_research  # type: ignore[method-assign]
    try:
        run_id = client.post(f"/api/oc/items/{code}/research", headers=oc_headers).json()["run_id"]
        report = client.get(f"/api/oc/items/{code}/research/report", headers=oc_headers)
        assert report.status_code == 200, report.text
        assert report.json()["report"]["run_id"] == run_id
        assert report.json()["report"]["total_found"] == 0
    finally:
        services.research.run_research = original  # type: ignore[method-assign]


def test_oc_research_report_not_found(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_item(client, oc_headers)
    assert client.get(f"/api/oc/items/{code}/research/report", headers=oc_headers).status_code == 404


# --- Media ------------------------------------------------------------------


def _seed_item(code: str, title: str) -> None:
    with get_db_session() as session:
        session.add(
            Item(code=code, kind="SELL", title=title, category_key="electronics",
                 condition_key="used", status="DRAFT")
        )
        session.commit()


def test_oc_media_ingest_marks_ai_and_verdict(client: TestClient, oc_headers: dict[str, str]) -> None:
    _seed_item("991", "Media Item")
    jpeg = _make_dummy_jpeg()
    resp = client.post(
        "/api/oc/items/991/photos",
        files=[("files", ("oc_ai.jpg", jpeg, "image/jpeg"))],
        data={"role": "ORIGINAL", "ai_generated": "true", "vlm_verdict": "agent note"},
        headers=oc_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ingested_count"] == 1

    from nextlife.storage.models import ItemPhoto

    with get_db_session() as session:
        by_role = {p.role: p for p in session.query(ItemPhoto).filter(ItemPhoto.item_code == "991").all()}
        assert set(by_role) == {"ORIGINAL", "PROCESSED", "PUBLISH"}
        assert all(p.ai_generated for p in by_role.values())
        assert by_role["ORIGINAL"].vlm_verdict.get("note") == "agent note"

    # A DEFECT photo must never be marked AI (kit rule: unretouched defect cover).
    resp2 = client.post(
        "/api/oc/items/991/photos",
        files=[("files", ("oc_defect.jpg", jpeg, "image/jpeg"))],
        data={"role": "DEFECT", "ai_generated": "true", "vlm_verdict": ""},
        headers=oc_headers,
    )
    assert resp2.status_code == 200, resp2.text
    with get_db_session() as session:
        assert session.get(ItemPhoto, resp2.json()["ids"][0]).ai_generated is False


def test_oc_media_build_kits(client: TestClient, oc_headers: dict[str, str]) -> None:
    _seed_item("992", "Build Item")
    jpeg = _make_dummy_jpeg()
    client.post(
        "/api/oc/items/992/photos",
        files=[("files", ("oc_build.jpg", jpeg, "image/jpeg"))],
        data={"role": "ORIGINAL"},
        headers=oc_headers,
    )
    resp = client.post("/api/oc/items/992/media-build", headers=oc_headers)
    assert resp.status_code == 200, resp.text
    kits = resp.json()["kits"]
    assert kits and any(v.get("photo_count", 0) > 0 for v in kits.values())


def test_oc_media_build_no_photos_400(client: TestClient, oc_headers: dict[str, str]) -> None:
    _seed_item("993", "Empty Item")
    assert client.post("/api/oc/items/993/media-build", headers=oc_headers).status_code == 400


# --- Draft / approval gate (3 mandatory scenarios + deal close) --------------


def test_publish_without_approval_409(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_item(client, oc_headers)
    _draft_build(client, oc_headers, code)
    resp = client.post(f"/api/oc/items/{code}/publish", json={"dry_run": True}, headers=oc_headers)
    assert resp.status_code == 409
    assert f"/ui/draft/{code}" in resp.json()["detail"]


def test_approval_then_draft_change_409(client: TestClient, oc_headers: dict[str, str],
                                        web_headers: dict[str, str]) -> None:
    code = _create_item(client, oc_headers)
    _approve_web(client, web_headers, code, _draft_build(client, oc_headers, code))
    _update_draft_web(client, web_headers, code, "Přepracovaný text pro test")
    resp = client.post(f"/api/oc/items/{code}/publish", json={"dry_run": True}, headers=oc_headers)
    assert resp.status_code == 409


def test_approval_then_publish_success(client: TestClient, oc_headers: dict[str, str],
                                       web_headers: dict[str, str]) -> None:
    code = _create_item(client, oc_headers)
    draft = client.post(f"/api/oc/items/{code}/draft", headers=oc_headers).json()
    assert draft["web_url"] == f"/ui/draft/{code}"
    _approve_web(client, web_headers, code, draft["payload_hash"])
    resp = client.post(f"/api/oc/items/{code}/publish", json={"dry_run": True}, headers=oc_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"]["successes"], "dry-run publish must succeed on all eligible channels"
    listings = client.get(f"/api/oc/items/{code}/listings", headers=oc_headers).json()
    assert listings["listings"], "publish must create active listings"


def test_deal_close_409_with_active_listing_and_stale_approval(
    client: TestClient, oc_headers: dict[str, str], web_headers: dict[str, str]
) -> None:
    code = _create_item(client, oc_headers)
    _approve_web(client, web_headers, code, _draft_build(client, oc_headers, code))
    pub = client.post(f"/api/oc/items/{code}/publish", json={"dry_run": True}, headers=oc_headers)
    assert pub.status_code == 200, pub.text
    _update_draft_web(client, web_headers, code, "Zmeněný text po publikaci")
    resp = client.post("/api/oc/deals/close", json={"code": code}, headers=oc_headers)
    assert resp.status_code == 409


def test_deal_close_without_active_listing_succeeds(client: TestClient, oc_headers: dict[str, str]) -> None:
    code = _create_item(client, oc_headers)
    with get_db_session() as session:
        session.get(Item, code).status = "SOLD"
        session.commit()
    resp = client.post("/api/oc/deals/close", json={"code": code}, headers=oc_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["archive"]["status"] == "ARCHIVED"


def test_deal_close_item_not_found(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.post("/api/oc/deals/close", json={"code": "ZZZ"}, headers=oc_headers).status_code == 404


# --- Outbox / inbox ----------------------------------------------------------


def test_oc_outbox_pull_empty(client: TestClient, oc_headers: dict[str, str]) -> None:
    resp = client.get("/api/oc/outbox", headers=oc_headers)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "count": 0, "messages": []}


def test_oc_outbox_ack_requires_evidence(client: TestClient, oc_headers: dict[str, str]) -> None:
    with get_db_session() as session:
        outbox = Outbox(
            id=str(uuid.uuid4()), transport="TELEGRAM", body="test message",
            idempotency_key="test-ack-key", state="PULLED", attempts=1, pulled_at=datetime.now(UTC),
        )
        session.add(outbox)
        session.commit()
        outbox_id = outbox.id
    assert client.post(
        f"/api/oc/outbox/{outbox_id}/ack", json={"delivery_evidence": {}}, headers=oc_headers
    ).status_code == 400
    resp = client.post(
        f"/api/oc/outbox/{outbox_id}/ack",
        json={"delivery_evidence": {"telegram_message_id": "123"}},
        headers=oc_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "SENT"


def test_oc_inbox_push_and_pending(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/inbox/pending", headers=oc_headers).json()["count"] == 0
    resp = client.post(
        "/api/oc/inbox",
        json={"message_id": "oc-msg-1", "from": "buyer@example.cz", "subject": "Dotaz", "body": "Ještě je to?"},
        headers=oc_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "status" in resp.json()["result"]


# --- Canaries ----------------------------------------------------------------


def test_oc_canaries_status_empty(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.get("/api/oc/canaries", headers=oc_headers).json()["channels"] == []


def test_oc_canaries_run_unknown_key_400(client: TestClient, oc_headers: dict[str, str]) -> None:
    assert client.post("/api/oc/canaries/run", json={"key": "nope_cz"}, headers=oc_headers).status_code == 400


def test_oc_canaries_run_direct_channels(client: TestClient, oc_headers: dict[str, str]) -> None:
    import nextlife.api.routes_openclaw_ops as ops
    from nextlife.canaries.engine import CanaryResult

    def fake_canary(session: Any, key: str, **kwargs: Any) -> CanaryResult:
        return CanaryResult(
            key=key, scenario="search", channel="DIRECT", state="OK", reason=None,
            artifact_path=f"/tmp/{key}.html", items_count=3, final_url="https://example.cz/",
        )

    original = ops.run_search_canary
    ops.run_search_canary = fake_canary  # type: ignore[assignment]
    try:
        direct_count = sum(1 for mp in load_config().marketplaces.values() if mp.access_mode == "DIRECT")
        resp = client.post("/api/oc/canaries/run", json={}, headers=oc_headers)
        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        assert len(results) == direct_count
        assert all(r["state"] == "OK" for r in results)
        single = client.post("/api/oc/canaries/run", json={"key": "bazos_cz"}, headers=oc_headers)
        assert single.status_code == 200
        assert len(single.json()["results"]) == 1
    finally:
        ops.run_search_canary = original  # type: ignore[assignment]
