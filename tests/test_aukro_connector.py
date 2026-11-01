"""Aukro connector tests (TZ M1 A.3).

Search runs against the site-internal JSON API
``POST /backend-web/api/offers/searchItemsCommon`` (the HTML endpoint serves
a JS SPA shell to plain HTTP). Live evidence:
``.a/reports/m1/probes/2026-10-04/aukro_cz_search_api_probe.json``.
"""

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.aukro import AukroConnector
from nextlife.connectors.base import CapabilityNotSupported
from nextlife.core.config import load_config

FIXTURE = Path(__file__).parent / "fixtures" / "aukro_sample.json"

# Recorded live response item (2026-10-04 probe, category notebooky-netbooky).
NB_ITEM = {
    "itemId": 7135774040,
    "itemName": "Lenovo ThinkPad L14 G3, i5 1245U /16 GB /256 NVMe, IRIS XE, záruka!",
    "auction": True,
    "buyNowActive": False,
    "seoUrl": "lenovo-thinkpad-l14-g3-i5-1245u-16-gb-256-nvme-iris-xe-zaruka",
    "price": {"amount": 4501, "currency": "CZK"},
    "buyNowPrice": {"amount": 0.0, "currency": "CZK"},
    "startingTime": "2026-10-01T20:42:03.101+02:00",
    "endingTime": "2026-10-11T20:41:49+02:00",
    "attributes": [
        {"attributeId": 48, "attributeName": "Stav zboží", "attributeValue": "Použité", "attributeValueId": 2, "position": 0}
    ],
}


def _mk_client(responses: list[dict]) -> httpx.Client:
    """MockTransport that answers POSTs in order, recording request bodies."""

    calls: list[dict] = []
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content.decode("utf-8")))
        body = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        return httpx.Response(
            body.get("status", 200),
            content=json.dumps(body["json"]).encode("utf-8"),
            headers={"content-type": "application/json"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=5.0)
    client.calls = calls  # type: ignore[attr-defined]
    return client


def _conn(client: httpx.Client) -> AukroConnector:
    config = load_config()
    return AukroConnector(config.marketplaces["aukro_cz"], client=client)


@pytest.fixture
def aukro_connector():
    config = load_config()
    return AukroConnector(config.marketplaces["aukro_cz"])


def test_aukro_search_mode_is_json_api(aukro_connector):
    """A.3: config drives the JSON transport; the canary prefix is the API path."""
    cfg = aukro_connector.config
    assert cfg.search_mode == "json"
    assert cfg.routes["search"] == "/backend-web/api/offers/searchItemsCommon"
    assert "backend-web/api/offers/searchItemsCommon" in aukro_connector._search_url()


def test_aukro_search_plain_text_query():
    client = _mk_client([{"json": {"content": [NB_ITEM], "links": [], "redirectUrl": None}}])
    listings = _conn(client).search("notebook dell xps")
    assert len(listings) == 1
    it = listings[0]
    assert it.title.startswith("Lenovo ThinkPad L14 G3")
    assert it.price == Decimal("4501")
    assert it.currency == "CZK"
    assert it.external_url == "https://aukro.cz/lenovo-thinkpad-l14-g3-i5-1245u-16-gb-256-nvme-iris-xe-zaruka-7135774040"
    # Ч-1: date from the API payload (startingTime), not invented.
    assert it.posted_at is not None
    assert (it.posted_at.year, it.posted_at.month, it.posted_at.day) == (2026, 10, 1)
    assert it.condition_text == "Použité"
    assert client.calls == [{"text": "notebook dell xps"}]  # type: ignore[attr-defined]


def test_aukro_search_follows_seo_redirect_to_category():
    """«notebook» → {content: [], redirectUrl: /notebooky-netbooky}; the
    connector follows it via categorySeoUrl — exactly like the SPA."""
    client = _mk_client(
        [
            {"json": {"content": [], "links": [], "redirectUrl": "/notebooky-netbooky"}},
            {"json": {"content": [NB_ITEM], "links": [], "redirectUrl": None}},
        ]
    )
    listings = _conn(client).search("notebook")
    assert len(listings) == 1
    assert client.calls == [  # type: ignore[attr-defined]
        {"text": "notebook"},
        {"categorySeoUrl": "notebooky-netbooky"},
    ]


def test_aukro_search_empty_after_redirect_is_empty():
    """No fabricated results: empty category follow-up stays empty."""
    client = _mk_client(
        [
            {"json": {"content": [], "links": [], "redirectUrl": "/notebooky-netbooky"}},
            {"json": {"content": [], "links": [], "redirectUrl": None}},
        ]
    )
    probe = _conn(client).probe_search("notebook")
    assert probe.matched == 0
    assert probe.items == []


def test_aukro_search_skips_items_without_price():
    broken = dict(NB_ITEM, itemId=1, price={"amount": 0, "currency": "CZK"})
    client = _mk_client([{"json": {"content": [NB_ITEM, broken], "links": [], "redirectUrl": None}}])
    listings = _conn(client).search("x")
    assert len(listings) == 1
    assert listings[0].price == Decimal("4501")


def test_aukro_publish_not_implemented_honest_error(aukro_connector):
    """Ч-5 (TZ M1): publication must go through the Aukro API with the
    owner's key (A.3). Direct HTML publish is forbidden; the connector must
    raise an honest error, not fabricate a PublishResult."""
    lot_data = {
        "title": "Sony PlayStation 5 Slim 1TB",
        "description": "Herní konzole v perfektním stavu.",
        "category": "electronics",
        "item_code": "555",
    }
    with pytest.raises(CapabilityNotSupported, match="post"):
        aukro_connector.publish(lot_data)


def test_aukro_publish_still_filters_internal_fields(aukro_connector):
    """The outgoing-field filter runs even though publish is not implemented."""
    lot_data = {
        "title": "Sony PlayStation 5",
        "description": "Konzole",
        "price_floor": 7000,
    }
    with pytest.raises(InternalFieldLeakageError):
        aukro_connector.publish(lot_data)


def test_aukro_update_and_unpublish_not_implemented(aukro_connector):
    """Edit/archive are not implemented (same reason as publish)."""
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    with pytest.raises(CapabilityNotSupported, match="edit"):
        aukro_connector.update("aukro-555-123", lot_data)
    with pytest.raises(CapabilityNotSupported, match="archive"):
        aukro_connector.unpublish("aukro-555-123")


def test_aukro_parses_sample_fixture(aukro_connector):
    """Fixture: live JSON API response recorded 2026-10-04
    (.a/reports/m1/probes/2026-10-04/aukro_cz_search_api_probe.json,
    step 2 — category notebooky-netbooky, 20 items). D.2: refresh > 30 days
    (→ 2026-11-03)."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(payload["content"]) == 20
    listings = aukro_connector.search_with_json(FIXTURE.read_text(encoding="utf-8"))
    assert len(listings) == 20
    first = listings[0]
    assert first.price == Decimal("4501")
    assert first.currency == "CZK"
    assert "Lenovo ThinkPad L14 G3" in first.title
    assert first.external_url.startswith("https://aukro.cz/lenovo-thinkpad")
    assert first.external_url.endswith("-7135774040")
    # Ч-1: every item carries startingTime in the payload.
    assert all(l.posted_at is not None for l in listings)
