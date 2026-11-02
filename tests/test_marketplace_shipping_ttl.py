"""Tests for per-marketplace shipping options compatibility and listing TTL."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.api.routes import check_auth
from nextlife.services.marketplace_shipping import (
    ALL_CARRIERS,
    filter_compatible_delivery_options,
    get_marketplace_allowed_carriers,
    get_marketplace_listing_ttl,
    is_carrier_compatible,
)
from nextlife.text.assembler import assemble_listing_text


def test_carrier_compatibility_defaults():
    # Czech marketplaces allow all domestic carriers including Zásilkovna and Balíkovna
    cz_carriers = get_marketplace_allowed_carriers("bazos_cz")
    assert "zasilkovna" in cz_carriers
    assert "balikovna" in cz_carriers
    assert "pickup" in cz_carriers
    assert "gls" in cz_carriers

    # Germany (Kleinanzeigen) does not allow Zásilkovna or Balíkovna
    de_carriers = get_marketplace_allowed_carriers("kleinanzeigen_de")
    assert "zasilkovna" not in de_carriers
    assert "balikovna" not in de_carriers
    assert "pickup" in de_carriers
    assert "gls" in de_carriers
    assert "cpost" in de_carriers

    # Austria (Willhaben)
    at_carriers = get_marketplace_allowed_carriers("willhaben_at")
    assert "zasilkovna" not in at_carriers
    assert "balikovna" not in at_carriers
    assert "gls" in at_carriers


def test_filter_compatible_delivery_options():
    options = [
        {"carrier_key": "pickup", "name": "Личная встреча", "buyer_total": 0, "currency": "Kč"},
        {"carrier_key": "zasilkovna", "name": "Zásilkovna", "buyer_total": 89, "currency": "Kč"},
        {"carrier_key": "gls", "name": "GLS", "buyer_total": 69, "currency": "Kč"},
    ]

    # For Bazoš: all 3 compatible
    comp_cz, incomp_cz = filter_compatible_delivery_options(options, "bazos_cz")
    assert len(comp_cz) == 3
    assert len(incomp_cz) == 0

    # For Kleinanzeigen: Zásilkovna is filtered out into incompatible
    comp_de, incomp_de = filter_compatible_delivery_options(options, "kleinanzeigen_de")
    assert len(comp_de) == 2
    assert [c["carrier_key"] for c in comp_de] == ["pickup", "gls"]
    assert len(incomp_de) == 1
    assert incomp_de[0]["carrier_key"] == "zasilkovna"


def test_text_assembler_excludes_incompatible_deliveries():
    item_data = {
        "title": "Retro Camera Olympus OM-1",
        "condition_grade": "A",
        "description": "Functional vintage camera in good shape.",
    }
    deliveries = [
        {"carrier_key": "zasilkovna", "name": "Zásilkovna", "buyer_total": 89, "currency": "Kč"},
        {"carrier_key": "gls", "name": "GLS", "buyer_total": 120, "currency": "Kč"},
    ]

    # Czech marketplace includes Zásilkovna
    cz_res = assemble_listing_text(item_data, deliveries, marketplace_key="bazos_cz")
    assert "Zásilkovna" in cz_res.body
    assert "GLS" in cz_res.body

    # German marketplace excludes Zásilkovna
    de_res = assemble_listing_text(item_data, deliveries, marketplace_key="kleinanzeigen_de")
    assert "Zásilkovna" not in de_res.body
    assert "GLS" in de_res.body


def test_marketplace_listing_ttl():
    # Defaults
    assert get_marketplace_listing_ttl("bazos_cz") == 60
    assert get_marketplace_listing_ttl("sbazar_cz") == 30
    assert get_marketplace_listing_ttl("kleinanzeigen_de") == 60

    # Custom override
    settings = {
        "listing_ttl_days": 45,
        "marketplace_overrides": {
            "bazos_cz": {"listing_ttl_days": 90},
            "unknown_mp": {},
        },
    }
    assert get_marketplace_listing_ttl("bazos_cz", settings=settings) == 90
    assert get_marketplace_listing_ttl("unknown_mp", settings=settings) == 45


def test_settings_save_marketplace_shipping_and_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nextlife.services.marketplace_shipping.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("nextlife.services.llm_filter.get_data_dir", lambda: tmp_path)

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    # Save per-marketplace settings
    resp = client.post(
        "/ui/settings/marketplaces",
        data={
            "listing_ttl_days": "30",
            "mp_bazos_cz_search": "on",
            "mp_bazos_cz_publish": "on",
            "mp_bazos_cz_ttl": "45",
            "mp_bazos_cz_carrier_pickup": "on",
            "mp_bazos_cz_carrier_zasilkovna": "on",
            "mp_bazos_cz_carrier_gls": "on",
            # Kleinanzeigen: allow only gls and pickup
            "mp_kleinanzeigen_de_search": "on",
            "mp_kleinanzeigen_de_ttl": "75",
            "mp_kleinanzeigen_de_carrier_pickup": "on",
            "mp_kleinanzeigen_de_carrier_gls": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "notice=marketplaces_saved" in resp.headers["location"]

    # Verify settings persisted
    settings_file = tmp_path / "settings.json"
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    ov = saved.get("marketplace_overrides", {})
    assert ov["bazos_cz"]["listing_ttl_days"] == 45
    assert set(ov["bazos_cz"]["allowed_carriers"]) == {"pickup", "zasilkovna", "gls"}
    assert ov["kleinanzeigen_de"]["listing_ttl_days"] == 75
    assert set(ov["kleinanzeigen_de"]["allowed_carriers"]) == {"pickup", "gls"}

    # Verify UI page renders with the controls
    get_resp = client.get("/ui/settings?tab=marketplaces")
    assert get_resp.status_code == 200
    assert 'data-testid="input-mp-ttl-bazos_cz"' in get_resp.text
    assert 'data-testid="cb-mp-carrier-bazos_cz-zasilkovna"' in get_resp.text
    assert 'data-testid="cb-mp-carrier-kleinanzeigen_de-gls"' in get_resp.text
