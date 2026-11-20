"""Facebook Marketplace connector tests (TZ-connectors-add-4).

Honest placeholder: search is blocked by a login wall (no public search
endpoint). The connector must raise a classified login_wall error, never
fabricate listings, and the probe must report the blocked state.
"""

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.connectors.base import CapabilityNotSupported, ConnectorError
from nextlife.connectors.facebook import (
    FacebookLoginWallError,
    FacebookMarketplaceConnector,
)
from nextlife.core.config import load_config


@pytest.fixture
def fb_connector():
    config = load_config()
    return FacebookMarketplaceConnector(config.marketplaces["facebook_marketplace"])


def test_fb_search_raises_login_wall(fb_connector):
    """Search must raise a classified login_wall error (never fabricate)."""
    with pytest.raises(FacebookLoginWallError):
        fb_connector.search("notebook")
    # The login wall error is a ConnectorError (wizard reports it as an error).
    with pytest.raises(ConnectorError):
        fb_connector.search("notebook")


def test_fb_search_error_message_names_login_wall(fb_connector):
    with pytest.raises(FacebookLoginWallError, match="login_wall"):
        fb_connector.search("notebook")


def test_fb_probe_reports_blocked_state(fb_connector):
    """The probe reports the blocked state (no items, a login_wall
    network_error) rather than raising — so the canary records it."""
    probe = fb_connector.probe_search("notebook")
    assert probe.items == []
    assert probe.matched == 0
    assert probe.http_status is None
    assert probe.network_error is not None
    assert "login_wall" in probe.network_error


def test_fb_publish_dry_run_and_auth_check(fb_connector):
    """Publishing on Facebook Marketplace supports dry-run package generation."""
    lot_data = {"title": "HP Omen 15", "description": "Notebook.", "item_code": "702"}
    dry_res = fb_connector.publish(lot_data, dry_run=True)
    assert dry_res.external_id.startswith("dry_fb_")
    assert dry_res.evidence["dry_run"] is True


def test_fb_publish_still_filters_internal_fields(fb_connector):
    lot_data = {"title": "HP Omen", "description": "Popis", "price_floor": 15000}
    with pytest.raises(InternalFieldLeakageError):
        fb_connector.publish(lot_data)


def test_fb_update_and_unpublish_implemented(fb_connector):
    lot_data = {"title": "Updated title", "description": "Updated desc"}
    res = fb_connector.update("fb-123", lot_data)
    assert res.evidence["dry_run"] is True
    assert fb_connector.unpublish("fb-123") is True
