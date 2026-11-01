"""Marketplace connector registry."""

import hashlib
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from nextlife.connectors.annonce import AnnonceConnector
from nextlife.connectors.aukro import AukroConnector
from nextlife.connectors.base import BaseConnector, ConnectorConfigError
from nextlife.connectors.bazos import BazosConnector
from nextlife.connectors.facebook import FacebookMarketplaceConnector
from nextlife.connectors.hyperinzerce import HyperinzerceConnector
from nextlife.connectors.kleinanzeigen import KleinanzeigenConnector
from nextlife.connectors.olx import OlxConnector
from nextlife.connectors.sbazar import SbazarConnector
from nextlife.connectors.trhknih import TrhKnihConnector
from nextlife.connectors.vinted import VintedConnector
from nextlife.connectors.willhaben import WillhabenConnector
from nextlife.core.config import ConfigBundle, MarketplaceConfig
from nextlife.storage.models import Marketplace

CONNECTOR_MAP: dict[str, type[BaseConnector]] = {
    "bazos_cz": BazosConnector,
    "sbazar_cz": SbazarConnector,
    "aukro_cz": AukroConnector,
    # TZ M1 A.4: подключение по шаблону Bazoš (live-verified 2026-10-04)
    "hyperinzerce_cz": HyperinzerceConnector,
    "annonce_cz": AnnonceConnector,
    # TZ M1 A.5: только категория «книги» (allowed_categories, live-verified 2026-10-04)
    "trhknih_cz": TrhKnihConnector,
    # Owner decision 2026-10-10: Vinted publish via web (skeleton, dry-run only)
    "vinted": VintedConnector,
    # TZ-connectors-add-4: web search connectors (2026-10-22)
    "kleinanzeigen_de": KleinanzeigenConnector,
    "olx_pl": OlxConnector,
    "willhaben_at": WillhabenConnector,
    # TZ-connectors-add-4: honest placeholder (login wall, no public search)
    "facebook_marketplace": FacebookMarketplaceConnector,
}


def _registry_capabilities(mp_cfg: MarketplaceConfig) -> dict[str, Any]:
    """Capabilities of the marketplace registry row (TZ M1 A.5, TZ UI §2.4).

    The registry row also carries the category restriction when the
    marketplace declares one (``allowed_categories``, e.g. TrhKnih = books
    only), so the restriction is visible in the marketplace registry, not
    only in the config file.

    Guarantees canonical presence of the 8 capability flags (§2.4):
    search, publish, update, unpublish, relist, messages_read, messages_send, delivery_parse.
    """
    caps = dict(mp_cfg.capabilities)
    default_flags = {
        "search": False,
        "publish": False,
        "update": False,
        "unpublish": False,
        "relist": False,
        "messages_read": False,
        "messages_send": False,
        "delivery_parse": False,
    }
    for flag, val in default_flags.items():
        if flag not in caps:
            if flag == "publish" and "post" in caps:
                caps["publish"] = bool(caps["post"])
            else:
                caps[flag] = val
        else:
            caps[flag] = bool(caps[flag])

    allowed = mp_cfg.allowed_categories
    if allowed:
        caps["allowed_categories"] = list(allowed)
    return caps


def sync_marketplaces(session: Session, config: ConfigBundle) -> None:
    """Sync marketplace registry from config YAML files into database table."""
    for key, mp_cfg in config.marketplaces.items():
        cfg_hash = hashlib.sha256(
            json.dumps(mp_cfg.model_dump(), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        existing = session.get(Marketplace, key)
        if existing is None:
            mp = Marketplace(
                key=key,
                title=mp_cfg.title,
                country=mp_cfg.country,
                currency=mp_cfg.currency,
                access_mode=mp_cfg.access_mode,
                capabilities=_registry_capabilities(mp_cfg),
                enabled=(mp_cfg.access_mode == "DIRECT"),
                config_hash=cfg_hash,
            )
            session.add(mp)
        else:
            existing.title = mp_cfg.title
            existing.country = mp_cfg.country
            existing.currency = mp_cfg.currency
            existing.access_mode = mp_cfg.access_mode
            existing.capabilities = _registry_capabilities(mp_cfg)
            existing.config_hash = cfg_hash
            if mp_cfg.access_mode == "ANDROID":
                existing.enabled = False
    session.flush()


def get_connector(
    key: str,
    config: ConfigBundle,
    client: httpx.Client | None = None,
) -> BaseConnector:
    if key not in config.marketplaces:
        raise ConnectorConfigError(f"Marketplace '{key}' not found in configuration.")

    connector_cls = CONNECTOR_MAP.get(key)
    if not connector_cls:
        raise ConnectorConfigError(f"No connector implementation registered for key '{key}'.")

    mp_cfg = config.marketplaces[key]
    return connector_cls(config=mp_cfg, client=client)
