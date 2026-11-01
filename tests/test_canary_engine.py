"""Channel canary engine tests (TZ M1 D.1, Ч-1..Ч-3, mutations D.3-1..3).

DoD:
- live probe saves raw artifact and checks the extracted field schema
  (title, price, currency, link, date);
- protection page → BLOCKED (not «0 results») with the artifact (mutation 2);
- unknown redirect → BLOCKED with the final URL (mutation 3);
- broken price selector → BLOCKED naming the field (mutation 1);
- empty answer after a successful probe → BLOCKED;
- owner notification with artifact on transition to BLOCKED;
- publishing on a blocked channel is refused.
"""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select

from conftest import clean_database

from nextlife.canaries.engine import (
    _check_schema,
    detect_protection_marker,
    is_direct_channel_blocked,
    run_search_canary,
)
from nextlife.connectors.base import PublishResult, RawListing
from nextlife.connectors.registry import sync_marketplaces
from nextlife.core.config import load_config
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.publish import publish_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import ChannelCanary, Item, Listing, Lot, Outbox

SBAZAR_FIXTURE = Path(__file__).parent / "fixtures" / "sbazar_sample.html"
SBAZAR_HTML = SBAZAR_FIXTURE.read_text(encoding="utf-8")


def _mock_client(html: str, *, status: int = 200, redirect_to: str | None = None) -> httpx.Client:
    """Mock transport; with redirect_to the first request gets a 301 (real follow)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if redirect_to and str(request.url) != redirect_to:
            return httpx.Response(301, headers={"location": redirect_to}, request=request)
        return httpx.Response(status, text=html, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=5.0)


@pytest.fixture(autouse=True)
def _clean_db():
    with get_db_session() as session:
        clean_database(session)
        cfg = load_config()
        sync_marketplaces(session, cfg)
        session.commit()
    yield


def test_canary_success_marks_ok_and_saves_artifact(tmp_path):
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(SBAZAR_HTML), probe_dir=tmp_path
        )
    assert res.state == "OK", res.reason
    assert res.items_count == 2  # «Dohodou» card is skipped by the parser
    assert res.final_url.endswith("/hledej/notebook")
    assert Path(res.artifact_path).exists()
    with get_db_session() as session:
        row = session.scalar(select(ChannelCanary).where(ChannelCanary.marketplace_key == "sbazar_cz"))
        assert row is not None
        assert row.state == "OK"
        assert row.last_success_at is not None
        assert row.last_success_count == 2
        assert row.date_absent_on_site is True  # Sbazar cards expose no date
        assert session.query(Outbox).count() == 0  # no notification on OK


def test_canary_protection_page_blocked_not_zero_results(tmp_path):
    """Mutation D.3-2: challenge page → BLOCKED, never «0 results»."""
    challenge_html = (
        "<html><head><title>Just a moment...</title></head>"
        '<body><div id="challenge-platform">cf-challenge</div></body></html>'
    )
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(challenge_html), probe_dir=tmp_path
        )
    assert res.state == "BLOCKED"
    assert res.reason is not None and res.reason.startswith("protection_page")
    assert res.items_count == 0
    assert Path(res.artifact_path).exists()  # artifact for the owner (Ч-3)
    with get_db_session() as session:
        outbox = session.scalars(select(Outbox)).all()
        assert len(outbox) == 1
        assert "sbazar_cz" in outbox[0].body
        assert res.artifact_path in outbox[0].body


def test_canary_cf_beacon_on_result_page_is_not_protection(tmp_path):
    """Ч-3 nuance (live-verified 2026-10-04, TrhKnih): ordinary result pages
    inline the Cloudflare real-user beacon whose URL contains the substring
    'challenge-platform'. A page that actually carries the site's result
    cards is a result page, not an interstitial — must not BLOCK."""
    beacon_html = SBAZAR_HTML + (
        "<script>window.__CF$cv$params={u:'x'};"
        "var s=document.createElement('script');"
        "s.src='/cdn-cgi/challenge-platform/abc/orchestrate.js';</script>"
    )
    assert detect_protection_marker(beacon_html) == "challenge-platform"
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(beacon_html), probe_dir=tmp_path
        )
    assert res.state == "OK", res.reason
    assert res.items_count == 2
    # the interstitial twin (same marker, no result cards) is still BLOCKED
    interstitial = '<html><body><script s="/cdn-cgi/challenge-platform/x.js"></script></body></html>'
    with get_db_session() as session:
        cfg = load_config()
        res2 = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(interstitial), probe_dir=tmp_path
        )
    assert res2.state == "BLOCKED" and res2.reason is not None and res2.reason.startswith("protection_page")


def test_canary_unknown_redirect_blocked_with_final_url(tmp_path):
    """Mutation D.3-3: redirect to an unknown address → BLOCKED with final URL."""
    seo_landing = "https://www.sbazar.cz/lp/nejlepsi-notebooky"
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session,
            "sbazar_cz",
            config=cfg,
            http_client=_mock_client(SBAZAR_HTML, redirect_to=seo_landing),
            probe_dir=tmp_path,
        )
    assert res.state == "BLOCKED"
    assert res.reason is not None and res.reason.startswith("unknown_redirect")
    assert seo_landing in res.reason


def test_canary_broken_price_selector_names_field(tmp_path):
    """Mutation D.3-1: broken Sbazar price selector → canary FAIL naming the field."""
    with get_db_session() as session:
        cfg = load_config()
        cfg.marketplaces["sbazar_cz"].selectors["price"] = ".nonexistent-price-class"
        res = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(SBAZAR_HTML), probe_dir=tmp_path
        )
    assert res.state == "BLOCKED"
    assert res.reason is not None
    assert "field=price" in res.reason  # cards matched but 0 parsable
    assert "schema_drift" in res.reason


def test_canary_empty_after_success_blocked(tmp_path):
    empty_html = "<html><body><ul data-test='offer-list'></ul></body></html>"
    with get_db_session() as session:
        cfg = load_config()
        first = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(SBAZAR_HTML), probe_dir=tmp_path
        )
        assert first.state == "OK"
        second = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(empty_html), probe_dir=tmp_path
        )
    assert second.state == "BLOCKED"
    assert second.reason == "empty_after_success"


def test_canary_empty_first_probe_blocked(tmp_path):
    empty_html = "<html><body><ul data-test='offer-list'></ul></body></html>"
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(empty_html), probe_dir=tmp_path
        )
    assert res.state == "BLOCKED"
    assert res.reason == "empty_first_probe"


def test_canary_http_error_blocked(tmp_path):
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client("<html></html>", status=503), probe_dir=tmp_path
        )
    assert res.state == "BLOCKED"
    assert res.reason == "http_503"


def test_canary_no_repeat_notification_while_blocked(tmp_path):
    broken = "<html><body><div id='challenge-platform'>cf-challenge</div></body></html>"
    with get_db_session() as session:
        cfg = load_config()
        first = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(broken), probe_dir=tmp_path
        )
        second = run_search_canary(
            session, "sbazar_cz", config=cfg, http_client=_mock_client(broken), probe_dir=tmp_path
        )
    assert first.state == "BLOCKED"
    assert second.state == "BLOCKED"
    with get_db_session() as session:
        assert session.query(Outbox).count() == 1  # transition notified once


def _item(posted_at=None, price=Decimal("100"), title="Notebook", url="https://x.cz/i/1", currency="CZK"):
    return RawListing(
        title=title,
        price=price,
        currency=currency,
        external_url=url,
        condition_text=None,
        posted_at=posted_at,
        raw={},
    )


def test_schema_check_requires_date_when_site_exposes_it():
    reason = _check_schema([_item(posted_at=None)], date_absent_on_site=False)
    assert reason is not None and "field=date" in reason


def test_schema_check_forbids_fabricated_date_when_site_has_none():
    """Ч-1: a parser that invents a date for a site without dates must fail the canary."""
    from datetime import UTC, datetime

    reason = _check_schema([_item(posted_at=datetime.now(UTC))], date_absent_on_site=True)
    assert reason is not None and "field=date" in reason


def test_schema_check_rejects_missing_price_and_relative_link():
    assert "field=price" in _check_schema([_item(price=Decimal("0"))], date_absent_on_site=True)
    assert "field=link" in _check_schema([_item(url="/inzerat/1")], date_absent_on_site=True)


def test_parse_listing_date_formats():
    """Ч-1: dates parsed from card markup only — live formats from 2026-10-03/04 probes."""
    from nextlife.connectors.base import BaseConnector

    # Bazoš: '-TOP- [4.10. 2026]' (space before year, live artifact 2026-10-04).
    d = BaseConnector.parse_listing_date("-TOP- [4.10. 2026]")
    assert d is not None and (d.year, d.month, d.day) == (2026, 10, 4)
    # Annonce: '29. 9. 2026' (space after every dot, live artifact 2026-10-04).
    d = BaseConnector.parse_listing_date("29. 9. 2026")
    assert d is not None and (d.year, d.month, d.day) == (2026, 9, 29)
    assert BaseConnector.parse_listing_date("dnes 14:32") is not None
    assert BaseConnector.parse_listing_date("včera 09:05") is not None
    assert BaseConnector.parse_listing_date("2026-10-04T12:00") is not None
    assert BaseConnector.parse_listing_date("bez data") is None
    assert BaseConnector.parse_listing_date(None) is None


def test_bazos_live_fixture_parses_date(tmp_path):
    """Live Bazoš probe (2026-10-04): every card carries a parseable date."""
    live = Path(".a/reports/canaries/2026-10-04/bazos_cz_004031.html")
    if not live.exists():
        pytest.skip("live probe artifact not present")
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "bazos_cz", config=cfg, http_client=_mock_client(live.read_text(encoding="utf-8")), probe_dir=tmp_path
        )
    assert res.state == "OK", res.reason
    assert res.items_count >= 20


def test_detect_protection_marker():
    assert detect_protection_marker("<html>cf-turnstile widget</html>") == "cf-turnstile"
    # Ч-3 (live-verified 2026-10-04, annonce.cz): a captcha script include on
    # an ordinary result page is NOT protection; the challenge widget markup is.
    assert (
        detect_protection_marker('<html><script src="https://www.google.com/recaptcha/api.js"></script></html>')
        is None
    )
    assert detect_protection_marker('<html><div class="g-recaptcha"></div></html>') == "g-recaptcha"
    assert detect_protection_marker("<html>ordinary results</html>") is None


def test_is_direct_channel_blocked_returns_reason():
    with get_db_session() as session:
        assert is_direct_channel_blocked(session, "sbazar_cz") is None
        session.add(
            ChannelCanary(
                marketplace_key="sbazar_cz",
                scenario="search",
                channel="DIRECT",
                state="BLOCKED",
                block_reason="protection_page: marker=datadome",
            )
        )
        session.commit()
        assert is_direct_channel_blocked(session, "sbazar_cz") == "protection_page: marker=datadome"


def test_completeness_control_all_checks_fire(tmp_path):
    """D.3-9: контроль полноты — каждая проверка канарейки срабатывает.

    Удаление любой проверки из кода движка меняет reason для её сценария —
    этот прогон становится красным.
    """
    empty_html = "<html><body><ul data-test='offer-list'></ul></body></html>"
    challenge = (
        "<html><head><title>Just a moment...</title></head>"
        "<body>cf-challenge</body></html>"
    )
    seo_landing = "https://www.sbazar.cz/lp/nejlepsi-notebooky"

    def run(client, *, clean: bool = True, mutate_cfg=None) -> "object":
        with get_db_session() as session:
            if clean:
                session.query(ChannelCanary).delete()
                session.commit()
            cfg = load_config()
            if mutate_cfg is not None:
                mutate_cfg(cfg)
            return run_search_canary(
                session, "sbazar_cz", config=cfg, http_client=client, probe_dir=tmp_path
            )

    # 1. network_error
    def raise_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    res = run(httpx.Client(transport=httpx.MockTransport(raise_handler), follow_redirects=True, timeout=5.0))
    assert res.state == "BLOCKED" and res.reason is not None and res.reason.startswith("network_error"), res.reason

    # 2. http_status
    res = run(_mock_client("<html></html>", status=503))
    assert res.state == "BLOCKED" and res.reason == "http_503", res.reason

    # 3. protection_page
    res = run(_mock_client(challenge))
    assert res.state == "BLOCKED" and res.reason is not None and res.reason.startswith("protection_page"), res.reason

    # 4. unknown_redirect
    res = run(_mock_client(SBAZAR_HTML, redirect_to=seo_landing))
    assert res.state == "BLOCKED" and res.reason is not None and res.reason.startswith("unknown_redirect"), res.reason

    # 5. schema_drift (broken price selector)
    def break_price(cfg):
        cfg.marketplaces["sbazar_cz"].selectors["price"] = ".nonexistent-price-class"

    res = run(_mock_client(SBAZAR_HTML), mutate_cfg=break_price)
    assert res.state == "BLOCKED" and res.reason is not None and res.reason.startswith("schema_drift: field=price"), res.reason

    # 6. empty_after_success (success first, then empty page)
    first = run(_mock_client(SBAZAR_HTML))
    assert first.state == "OK", first.reason
    res = run(_mock_client(empty_html), clean=False)
    assert res.state == "BLOCKED" and res.reason == "empty_after_success", res.reason

    # 7. empty_first_probe
    res = run(_mock_client(empty_html))
    assert res.state == "BLOCKED" and res.reason == "empty_first_probe", res.reason


AUKRO_FIXTURE = Path(__file__).parent / "fixtures" / "aukro_sample.json"
AUKRO_CATEGORY_RESPONSE = json.loads(AUKRO_FIXTURE.read_text(encoding="utf-8"))
AUKRO_REDIRECT_RESPONSE = {"content": [], "links": [], "redirectUrl": "/notebooky-netbooky"}


def _aukro_client(responses: list[dict]) -> httpx.Client:
    """MockTransport for the Aukro JSON API (POST), answers in order."""

    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        if "text_html" in body:
            return httpx.Response(body.get("status", 200), text=body["text_html"], request=request)
        return httpx.Response(
            body.get("status", 200),
            content=json.dumps(body["json"]).encode("utf-8"),
            headers={"content-type": "application/json"},
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=5.0)


def test_canary_json_mode_ok_follows_seo_redirect(tmp_path):
    """A.3: JSON-mode canary (Aukro) — «notebook» → redirectUrl →
    categorySeoUrl follow-up → OK with the category's items."""
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session,
            "aukro_cz",
            config=cfg,
            http_client=_aukro_client([{"json": AUKRO_REDIRECT_RESPONSE}, {"json": AUKRO_CATEGORY_RESPONSE}]),
            probe_dir=tmp_path,
        )
    assert res.state == "OK", res.reason
    assert res.items_count == len(AUKRO_CATEGORY_RESPONSE["content"])
    assert "/backend-web/api/offers/searchItemsCommon" in res.final_url
    assert Path(res.artifact_path).name.endswith(".json")  # JSON raw artifact
    with get_db_session() as session:
        row = session.scalar(select(ChannelCanary).where(ChannelCanary.marketplace_key == "aukro_cz"))
        assert row is not None
        assert row.state == "OK"
        assert row.last_success_count == len(AUKRO_CATEGORY_RESPONSE["content"])
        assert row.date_absent_on_site is False  # the API carries startingTime


def test_canary_json_mode_empty_after_redirect_blocked(tmp_path):
    """No fabricated results: empty category follow-up → honest empty."""
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session,
            "aukro_cz",
            config=cfg,
            http_client=_aukro_client([{"json": AUKRO_REDIRECT_RESPONSE}, {"json": {"content": [], "links": [], "redirectUrl": None}}]),
            probe_dir=tmp_path,
        )
    assert res.state == "BLOCKED"
    assert res.reason == "empty_first_probe"


def test_canary_json_mode_schema_drift_names_price(tmp_path):
    """Records present but unparsable (price missing) → schema_drift field=price."""
    broken = {"content": [{"itemId": 1, "itemName": "X", "seoUrl": "x", "price": None}], "links": [], "redirectUrl": None}
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "aukro_cz", config=cfg, http_client=_aukro_client([{"json": broken}]), probe_dir=tmp_path
        )
    assert res.state == "BLOCKED"
    assert res.reason is not None
    assert res.reason.startswith("schema_drift: field=price")
    assert "1 card(s) matched, 0 parsable" in res.reason


def test_canary_json_mode_protection_page_blocked(tmp_path):
    """If the API answers with a challenge page (HTML, 200) → protection_page."""
    challenge = '<html><head><title>Just a moment...</title></head><body>cf-challenge</body></html>'
    with get_db_session() as session:
        cfg = load_config()
        res = run_search_canary(
            session, "aukro_cz", config=cfg, http_client=_aukro_client([{"text_html": challenge}]), probe_dir=tmp_path
        )
    assert res.state == "BLOCKED"
    assert res.reason is not None and res.reason.startswith("protection_page")


def test_publish_refused_on_canary_blocked_channel():
    """D.1: publishing on a blocked channel is not executed."""
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Canary Blocked Publish",
            category_key="general",
            price=Decimal("1500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        session.add(
            ChannelCanary(
                marketplace_key="bazos_cz",
                scenario="search",
                channel="DIRECT",
                state="BLOCKED",
                block_reason="empty_after_success",
            )
        )
        session.commit()

        good_connector = MagicMock()
        good_connector.publish.return_value = PublishResult(
            external_id="bazos-1",
            external_url="https://www.bazos.cz/inzerat/bazos-1",
            evidence={"http_status": 200},
        )

        res = publish_lot(
            session=session,
            lot_code=lot_code,
            target_marketplaces=["bazos_cz"],
            connector_overrides={"bazos_cz": good_connector},
        )
        session.commit()

        assert "bazos_cz" in res["failures"]
        assert "Channel BLOCKED by canary" in res["failures"]["bazos_cz"]
        assert res["lot_status"] != "PUBLISHED"
        good_connector.publish.assert_not_called()
        assert session.scalar(select(Listing).where(Listing.item_code == item_code)) is None

        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        # не утекать в другие тестовые файлы: заблокированная строка канарейки
        # блокировала бы публикации на bazos_cz в test_publish_evidence и т.п.
        session.query(ChannelCanary).filter(
            ChannelCanary.marketplace_key == "bazos_cz"
        ).delete()
        session.commit()
