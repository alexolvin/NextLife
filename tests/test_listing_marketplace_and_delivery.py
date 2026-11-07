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
