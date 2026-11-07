"""Unit tests for newly added connectors: bazos_sk, bazar_cz, mimibazar_cz, allegro_cz."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from nextlife.connectors.allegro import AllegroConnector
from nextlife.connectors.bazar import BazarConnector
from nextlife.connectors.bazos import BazosConnector
from nextlife.connectors.ebay import EbayConnector
from nextlife.connectors.mimibazar import MimibazarConnector
from nextlife.connectors.registry import CONNECTOR_MAP
from nextlife.core.config import load_config


def test_registry_contains_new_connectors():
    assert "bazos_sk" in CONNECTOR_MAP
    assert "bazar_cz" in CONNECTOR_MAP
    assert "mimibazar_cz" in CONNECTOR_MAP
    assert "allegro_cz" in CONNECTOR_MAP
    assert "ebay" in CONNECTOR_MAP
    assert CONNECTOR_MAP["bazos_sk"] is BazosConnector
    assert CONNECTOR_MAP["bazar_cz"] is BazarConnector
    assert CONNECTOR_MAP["mimibazar_cz"] is MimibazarConnector
    assert CONNECTOR_MAP["allegro_cz"] is AllegroConnector
    assert CONNECTOR_MAP["ebay"] is EbayConnector


def test_bazar_connector_parsing():
    cfg = load_config().marketplaces["bazar_cz"]
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
    <div class="sale-item">
      <a href="/xad123-asus-tuf-7900-xtx.aspx">
        <h2 class="title">ASUS TUF RX 7900 XTX 24GB</h2>
        <div class="img-box"><img src="https://imga.bazar.cz/g/inzer/1.jpg" /></div>
      </a>
      <span class="price">26 000 Kč</span>
    </div>
    """
    mock_client.get.return_value = mock_resp
    connector = BazarConnector(cfg, mock_client)
    results = connector.search("7900 XTX")
    assert len(results) == 1
    assert results[0].title == "ASUS TUF RX 7900 XTX 24GB"
    assert results[0].price == Decimal("26000")
    assert results[0].currency == "CZK"
    assert "123" in results[0].external_url


def test_mimibazar_connector_parsing():
    cfg = load_config().marketplaces["mimibazar_cz"]
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "items": [
            {"id": 999, "title": "Dětský kočárek", "url": "https://www.mimibazar.cz/inzerat/999/kocarek"}
        ],
        "html": """
        <div class="card" id="card-999">
          <img src="//img-new.mimibazar.cz/test.jpg" />
          <div class="card__price price"><span class="price__main">3 500 Kč</span></div>
        </div>
        """,
    }
    mock_client.post.return_value = mock_resp
    connector = MimibazarConnector(cfg, mock_client)
    results = connector.search("kocarek")
    assert len(results) == 1
    assert results[0].title == "Dětský kočárek"
    assert results[0].price == Decimal("3500")
    assert results[0].currency == "CZK"
    assert results[0].raw["image_url"] == "https://img-new.mimibazar.cz/test.jpg"


def test_allegro_connector_bot_detection():
    cfg = load_config().marketplaces["allegro_cz"]
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Cloudflare Challenge"
    mock_client.get.return_value = mock_resp
    connector = AllegroConnector(cfg, mock_client)
    results = connector.search("7900 XTX")
    assert results == []


def test_ebay_connector_parsing():
    cfg = load_config().marketplaces["ebay"]
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
    <div class="s-item">
      <a class="s-item__link" href="https://www.ebay.com/itm/123456789">
        <span class="s-item__title">ASUS ROG Crosshair VIII Hero X570</span>
      </a>
      <span class="s-item__price">$249.99</span>
      <img class="s-item__image-img" src="https://i.ebayimg.com/images/g/test/s-l500.jpg" />
    </div>
    """
    mock_client.get.return_value = mock_resp
    connector = EbayConnector(cfg, mock_client)
    results = connector.search("asus x570")
    assert len(results) == 1
    assert results[0].title == "ASUS ROG Crosshair VIII Hero X570"
    assert results[0].price == Decimal("249.99")
    assert results[0].currency == "USD"
    assert "123456789" in results[0].external_url
    assert results[0].raw["image_url"] == "https://i.ebayimg.com/images/g/test/s-l500.jpg"

