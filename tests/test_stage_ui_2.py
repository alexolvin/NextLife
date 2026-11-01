"""Unit tests for Stage 2: Background worker, error catalog, batch reports and indicator (TZ §6.3, §3.1)."""

from decimal import Decimal
from unittest.mock import MagicMock

import httpx

from nextlife.connectors.base import CapabilityNotSupported
from nextlife.jobs.catalog import ERROR_CATALOG, classify_error
from nextlife.jobs.manager import get_batch_summary
from nextlife.jobs.worker import NETWORK_RETRY_DELAYS
from nextlife.storage.models import Job, Listing


def test_classify_error_mapping() -> None:
    # 1. Network errors
    c, t, h = classify_error(httpx.ConnectError("connection refused"))
    assert c == "NETWORK"
    assert h == ERROR_CATALOG["NETWORK"]["hint"]

    c, t, h = classify_error(OSError("socket timeout"))
    assert c == "NETWORK"

    # 2. Capability not supported
    c, t, h = classify_error(CapabilityNotSupported("operation not allowed"))
    assert c == "NOT_SUPPORTED"
    assert h == ERROR_CATALOG["NOT_SUPPORTED"]["hint"]

    # 3. Auth required
    c, t, h = classify_error(RuntimeError("401 Unauthorized: please login"))
    assert c == "AUTH_REQUIRED"
    assert h == ERROR_CATALOG["AUTH_REQUIRED"]["hint"]

    # 4. Captcha
    c, t, h = classify_error(RuntimeError("Cloudflare captcha challenge detected"))
    assert c == "CAPTCHA"

    # 5. Validation & category
    c, t, h = classify_error(ValueError("Category not accepted by portal"))
    assert c == "CATEGORY_INVALID"

    c, t, h = classify_error(ValueError("Title field validation failed"))
    assert c == "VALIDATION"

    # 6. Unknown
    c, t, h = classify_error(Exception("Some weird crash"))
    assert c == "UNKNOWN"


def test_retry_delays_policy() -> None:
    # 3 attempts with 1, 5, 15 min delays for network errors
    assert NETWORK_RETRY_DELAYS == [60, 300, 900]


def test_batch_summary_logic() -> None:
    session = MagicMock()
    batch_id = "test-batch-1"

    job1 = Job(
        id="j1",
        batch_id=batch_id,
        kind="PUBLISH_LISTING",
        state="SUCCESS",
        payload={"listing_id": "l1", "marketplace_key": "bazos_cz"},
    )
    job2 = Job(
        id="j2",
        batch_id=batch_id,
        kind="PUBLISH_LISTING",
        state="FAILED",
        last_error="[AUTH_REQUIRED] Session expired",
        payload={"listing_id": "l2", "marketplace_key": "aukro_cz"},
    )
    job3 = Job(
        id="j3",
        batch_id=batch_id,
        kind="PUBLISH_LISTING",
        state="RUNNING",
        payload={"listing_id": "l3", "marketplace_key": "sbazar_cz"},
    )

    session.scalars.return_value.all.return_value = [job1, job2, job3]

    listing1 = Listing(
        id="l1",
        item_code="100",
        marketplace_key="bazos_cz",
        price=Decimal("500"),
        currency="CZK",
        price_base=Decimal("500"),
        status="PUBLISHED",
        title="L1 Title",
        external_url="https://pc.bazos.cz/inzerat/123",
    )
    listing2 = Listing(
        id="l2",
        item_code="100",
        marketplace_key="aukro_cz",
        price=Decimal("500"),
        currency="CZK",
        price_base=Decimal("500"),
        status="FAILED",
        title="L2 Title",
        last_error_code="AUTH_REQUIRED",
        last_error_hint="Войдите на площадку заново",
    )
    listing3 = Listing(
        id="l3",
        item_code="100",
        marketplace_key="sbazar_cz",
        price=Decimal("500"),
        currency="CZK",
        price_base=Decimal("500"),
        status="PUBLISHING",
        title="L3 Title",
    )

    def mock_get(cls, key):
        if key == "l1":
            return listing1
        if key == "l2":
            return listing2
        if key == "l3":
            return listing3
        return None

    session.get.side_effect = mock_get

    summary = get_batch_summary(session, batch_id)
    assert summary["batch_id"] == batch_id
    assert summary["total"] == 3
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert summary["running"] == 1
    assert summary["is_completed"] is False
    assert len(summary["items"]) == 3

    item1 = summary["items"][0]
    assert item1["state"] == "SUCCESS"
    assert item1["external_url"] == "https://pc.bazos.cz/inzerat/123"

    item2 = summary["items"][1]
    assert item2["state"] == "FAILED"
    assert item2["error_code"] == "AUTH_REQUIRED"
    assert item2["action_label"] == "Войти"
