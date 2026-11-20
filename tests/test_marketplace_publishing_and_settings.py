"""Tests for marketplace publishing capability, category trees, and Settings page checkboxes."""

import pytest
from starlette.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.connectors.bazar import BazarConnector
from nextlife.connectors.mimibazar import MimibazarConnector
from nextlife.connectors.base import ConnectorError
from nextlife.core.config import load_config
from nextlife.services.settings_store import load_settings, save_settings


@pytest.fixture
def client():
    return TestClient(create_app())


def _auth() -> dict[str, str]:
    from nextlife.core.config import load_config
    from nextlife.core.secrets import resolve_secret
    tok = resolve_secret(load_config().app.auth.web_token_secret_ref) or "test_token_secret"
    return {"Authorization": f"Bearer {tok}", "Cookie": f"nextlife_token={tok}"}


def test_bazar_cz_category_tree_and_publish():
    cfg = load_config()
    mp_cfg = cfg.marketplaces["bazar_cz"]
    assert mp_cfg.capabilities.get("post") is True
    assert mp_cfg.category_tree is not None
    assert len(mp_cfg.category_tree) >= 10

    conn = BazarConnector(mp_cfg)
    lot_data = {
        "title": "ASUS GeForce RTX 3080",
        "description": "Grafická karta ve skvělém stavu.",
        "category": "pocitace/Grafické karty",
        "price": "14000",
        "item_code": "801",
    }
    dry_res = conn.publish(lot_data, dry_run=True)
    assert dry_res.external_id.startswith("dry_bazar_")
    assert dry_res.evidence["dry_run"] is True

    # Live publish requires credentials
    with pytest.raises(ConnectorError, match="авторизация"):
        conn.publish(lot_data, dry_run=False)


def test_mimibazar_cz_category_tree_and_publish():
    cfg = load_config()
    mp_cfg = cfg.marketplaces["mimibazar_cz"]
    assert mp_cfg.capabilities.get("post") is True
    assert mp_cfg.category_tree is not None
    assert len(mp_cfg.category_tree) >= 7

    conn = MimibazarConnector(mp_cfg)
    lot_data = {
        "title": "Dětský kočárek Thule",
        "description": "Kočárek po jednom dítěti.",
        "category": "sport-a-kocarky/Kola a odrážedla",
        "price": "6000",
        "item_code": "802",
    }
    dry_res = conn.publish(lot_data, dry_run=True)
    assert dry_res.external_id.startswith("dry_mimibazar_")
    assert dry_res.evidence["dry_run"] is True

    # Live publish requires credentials
    with pytest.raises(ConnectorError, match="авторизация"):
        conn.publish(lot_data, dry_run=False)


def test_all_publishing_marketplaces_have_category_trees():
    cfg = load_config()
    target_marketplaces = [
        "bazos_cz", "bazos_sk", "sbazar_cz", "ebay", "aukro_cz", "vinted",
        "bazar_cz", "mimibazar_cz", "hyperinzerce_cz", "annonce_cz",
        "trhknih_cz", "facebook_marketplace",
    ]
    for key in target_marketplaces:
        mp = cfg.marketplaces.get(key)
        assert mp is not None, f"Marketplace {key} missing from config"
        assert mp.capabilities.get("post") is True, f"Marketplace {key} should have post: true"
        assert mp.category_tree is not None and len(mp.category_tree) > 0, (
            f"Marketplace {key} should have non-empty category_tree"
        )


def test_settings_search_only_marketplaces_disable_publish_checkbox(client):
    # GET /ui/settings?tab=marketplaces
    resp = client.get("/ui/settings?tab=marketplaces", headers=_auth())
    assert resp.status_code == 200
    html = resp.text

    # Search-only platforms must NOT have publish checkbox, TTL input, carriers, or unpublish/relist sys params
    for key in ("kleinanzeigen_de", "willhaben_at", "olx_pl"):
        assert f'name="mp_{key}_publish"' not in html, f"Search-only marketplace {key} must not have publish checkbox"
        assert f'data-testid="input-mp-ttl-{key}"' not in html, f"Search-only marketplace {key} must not have TTL input"
        assert f'data-testid="sys-params-{key}"' not in html, f"Search-only marketplace {key} must not have auto-unpublish sys params"
        assert f'data-testid="cb-mp-carrier-{key}-' not in html, f"Search-only marketplace {key} must not have carrier checkboxes"
        assert f'data-testid="search-only-notice-{key}"' in html, f"Search-only marketplace {key} must display search-only notice"

    # Publishing platforms MUST have publish checkbox, TTL input, and sys params
    for key in ("bazos_cz", "sbazar_cz", "bazar_cz", "mimibazar_cz", "hyperinzerce_cz", "annonce_cz", "trhknih_cz", "facebook_marketplace"):
        assert f'name="mp_{key}_publish"' in html, f"Publishing marketplace {key} must have publish checkbox"
        assert f'data-testid="input-mp-ttl-{key}"' in html, f"Publishing marketplace {key} must have TTL input"
        assert f'data-testid="sys-params-{key}"' in html, f"Publishing marketplace {key} must have sys params"


def test_settings_save_prevents_enabling_publish_for_search_only(client, tmp_path, monkeypatch):
    monkeypatch.setattr("nextlife.services.marketplace_shipping.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("nextlife.services.llm_filter.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("nextlife.services.settings_store.get_data_dir", lambda: tmp_path)

    # POST to /ui/settings/marketplaces trying to enable publish on search-only platforms
    post_data = {
        "mp_kleinanzeigen_de_search": "on",
        "mp_kleinanzeigen_de_publish": "on",
        "mp_willhaben_at_search": "on",
        "mp_willhaben_at_publish": "on",
        "mp_olx_pl_search": "on",
        "mp_olx_pl_publish": "on",
        "mp_bazar_cz_search": "on",
        "mp_bazar_cz_publish": "on",
    }
    resp = client.post("/ui/settings/marketplaces", data=post_data, headers=_auth(), follow_redirects=False)
    assert resp.status_code in (302, 303)

    settings = load_settings()
    overrides = settings.get("marketplace_overrides", {})
    assert overrides.get("kleinanzeigen_de", {}).get("publish") is False
    assert overrides.get("willhaben_at", {}).get("publish") is False
    assert overrides.get("olx_pl", {}).get("publish") is False
    assert overrides.get("bazar_cz", {}).get("publish") is True
