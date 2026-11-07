import pytest
from decimal import Decimal
from unittest.mock import MagicMock
from types import SimpleNamespace
from starlette.testclient import TestClient

from nextlife.api.routes import marketplace_url
from nextlife.services.listings import get_marketplace_base_url
from nextlife.connectors.terms_mapping import map_terms_to_marketplace


def test_get_marketplace_base_url_and_filter():
    # Test base URLs for known marketplaces
    assert get_marketplace_base_url("bazos_sk") == "https://www.bazos.sk"
    assert get_marketplace_base_url("ebay") == "https://www.ebay.com"
    assert get_marketplace_base_url("non_existent_mp") is None

    # Test marketplace_url filter
    # 1. Listing has explicit external_url
    l1 = {"external_url": "https://custom.com/item/1"}
    assert marketplace_url(l1) == "https://custom.com/item/1"

    # 2. Listing has None external_url, should fallback to marketplace base_url
    l2 = {"marketplace_key": "bazos_sk", "external_url": None}
    assert marketplace_url(l2) == "https://www.bazos.sk"

    l3 = SimpleNamespace(marketplace_key="ebay", external_url=None)
    assert marketplace_url(l3) == "https://www.ebay.com"


def test_map_terms_delivery_price_preserved():
    terms = map_terms_to_marketplace(
        marketplace_key="bazos_sk",
        delivery_options=[{"carrier_key": "pickup", "price": 0.0}, {"carrier_key": "zasilkovna", "price": 3.56}],
        payment_options=["cash", "bank_transfer"],
    )
    assert terms.marketplace_key == "bazos_sk"
    deliv_prices = {d["carrier_key"]: d["price"] for d in terms.mapped_delivery}
    assert deliv_prices["pickup"] == 0.0
    assert deliv_prices["zasilkovna"] == 3.56


def test_publish_listing_now_creates_job():
    from nextlife.services.listings import publish_listing_now
    from nextlife.storage.models import Listing, Job

    mock_session = MagicMock()
    mock_listing = SimpleNamespace(
        id="list-fail-1",
        item_code="130",
        marketplace_key="bazos_cz",
        status="FAILED",
        last_error_code="AUTH_REQUIRED",
        last_error_text="Session expired",
        last_error_hint="Login again",
    )
    mock_session.get.return_value = mock_listing

    res = publish_listing_now(mock_session, "list-fail-1")
    assert res["supported"] is True
    assert res["status"] == "QUEUED"
    assert mock_listing.status == "QUEUED"
    assert mock_listing.last_error_code is None
    assert mock_listing.last_error_text is None
    assert mock_session.add.called
    added_job = mock_session.add.call_args[0][0]
    assert added_job.kind == "PUBLISH_LISTING"
    assert added_job.payload["listing_id"] == "list-fail-1"

