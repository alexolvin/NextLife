"""Marketplace-specific shipping and listing TTL service (TZ §11 item 2, §3.3).

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextlife.core.config import get_data_dir

# All known carriers and their descriptive titles
ALL_CARRIERS: list[dict[str, str]] = [
    {"key": "pickup", "title": "Личная встреча / Osobní odběr", "desc": "Самовывоз / личная встреча на месте"},
    {"key": "zasilkovna", "title": "Zásilkovna / Packeta", "desc": "Доставка в пункты выдачи и Z-BOX (CZ, SK)"},
    {"key": "balikovna", "title": "Balíkovna (Česká pošta)", "desc": "Доставка в отделения и боксы (только CZ)"},
    {"key": "cpost", "title": "Česká pošta", "desc": "Balík Do ruky / международная посылка"},
    {"key": "ppl", "title": "PPL CZ", "desc": "Parcelshop / Parcelbox / курьер (CZ, DHL сеть)"},
    {"key": "gls", "title": "GLS", "desc": "ParcelShop / курьерская доставка по Европе (CZ, DE, AT, PL)"},
    {"key": "courier", "title": "Курьер DPD / PPL", "desc": "Адресная курьерская доставка"},
]

CARRIER_MAP: dict[str, dict[str, str]] = {c["key"]: c for c in ALL_CARRIERS}

# Default carrier compatibility based on country and marketplace capabilities
DEFAULT_MARKETPLACE_CARRIERS: dict[str, list[str]] = {
    "bazos_cz": ["pickup", "zasilkovna", "balikovna", "cpost", "ppl", "gls", "courier"],
    "sbazar_cz": ["pickup", "zasilkovna", "balikovna", "cpost", "ppl", "gls", "courier"],
    "aukro_cz": ["pickup", "zasilkovna", "balikovna", "cpost", "ppl", "gls", "courier"],
    "annonce_cz": ["pickup", "zasilkovna", "balikovna", "cpost", "ppl", "gls", "courier"],
    "hyperinzerce_cz": ["pickup", "zasilkovna", "balikovna", "cpost", "ppl", "gls", "courier"],
    "trhknih_cz": ["pickup", "zasilkovna", "balikovna", "cpost"],
    "facebook_marketplace": ["pickup", "zasilkovna", "balikovna", "cpost", "ppl", "gls", "courier"],
    "vinted": ["pickup", "zasilkovna", "balikovna", "ppl", "gls"],
    "vinted_cz": ["pickup", "zasilkovna", "balikovna", "ppl", "gls"],
    # Foreign marketplaces: Zásilkovna and Balíkovna are incompatible
    "kleinanzeigen_de": ["pickup", "gls", "courier", "cpost"],
    "willhaben_at": ["pickup", "gls", "courier", "cpost"],
    "olx_pl": ["pickup", "gls", "courier", "cpost"],
}

DEFAULT_MARKETPLACE_TTL: dict[str, int] = {
    "bazos_cz": 60,
    "sbazar_cz": 30,
    "aukro_cz": 30,
    "annonce_cz": 30,
    "hyperinzerce_cz": 30,
    "trhknih_cz": 60,
    "facebook_marketplace": 30,
    "kleinanzeigen_de": 60,
    "willhaben_at": 30,
    "olx_pl": 30,
    "vinted": 30,
    "vinted_cz": 30,
}


def _load_settings() -> dict[str, Any]:
    path = get_data_dir() / "settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_marketplace_allowed_carriers(
    marketplace_key: str,
    settings: dict[str, Any] | None = None,
) -> list[str]:
    """Return list of allowed carrier keys for the given marketplace."""
    cfg = settings if settings is not None else _load_settings()
    overrides = cfg.get("marketplace_overrides", {})
    if isinstance(overrides, dict) and marketplace_key in overrides:
        mp_ov = overrides[marketplace_key]
        if isinstance(mp_ov, dict) and "allowed_carriers" in mp_ov:
            raw = mp_ov["allowed_carriers"]
            if isinstance(raw, list):
                return [str(c).strip().lower() for c in raw if str(c).strip().lower() in CARRIER_MAP]

    # Fallback to default mapping or all carriers for unknown
    return list(DEFAULT_MARKETPLACE_CARRIERS.get(marketplace_key, ["pickup", "gls", "cpost", "courier"]))


def get_marketplace_listing_ttl(
    marketplace_key: str,
    settings: dict[str, Any] | None = None,
) -> int:
    """Return listing TTL in days for the given marketplace."""
    cfg = settings if settings is not None else _load_settings()
    overrides = cfg.get("marketplace_overrides", {})
    if isinstance(overrides, dict) and marketplace_key in overrides:
        mp_ov = overrides[marketplace_key]
        if isinstance(mp_ov, dict) and "listing_ttl_days" in mp_ov:
            try:
                ttl = int(mp_ov["listing_ttl_days"])
                if ttl > 0:
                    return ttl
            except (ValueError, TypeError):
                pass

    # Marketplace default from dict
    if marketplace_key in DEFAULT_MARKETPLACE_TTL:
        return DEFAULT_MARKETPLACE_TTL[marketplace_key]

    # Global fallback
    return int(cfg.get("listing_ttl_days", 30))


def is_carrier_compatible(carrier_key: str, marketplace_key: str, settings: dict[str, Any] | None = None) -> bool:
    """Check if carrier is compatible with the specified marketplace."""
    allowed = get_marketplace_allowed_carriers(marketplace_key, settings=settings)
    return carrier_key.lower().strip() in allowed


def filter_compatible_delivery_options(
    options: list[Any],
    marketplace_key: str,
    settings: dict[str, Any] | None = None,
) -> tuple[list[Any], list[Any]]:
    """Split a list of delivery options into (compatible, incompatible) for marketplace.

    Works with both dicts (e.g. {'carrier_key': 'zasilkovna', ...}) and ORM DeliveryOption objects.
    """
    allowed = set(get_marketplace_allowed_carriers(marketplace_key, settings=settings))
    compatible: list[Any] = []
    incompatible: list[Any] = []

    for opt in options:
        if isinstance(opt, dict):
            ckey = str(opt.get("carrier_key") or opt.get("key") or opt.get("name") or "").lower().strip()
        else:
            ckey = str(getattr(opt, "carrier_key", "") or "").lower().strip()

        # Normalize names like 'Zásilkovna' -> 'zasilkovna' if key missing
        if ckey not in CARRIER_MAP:
            for k in CARRIER_MAP:
                if k in ckey:
                    ckey = k
                    break

        if ckey in allowed:
            compatible.append(opt)
        else:
            incompatible.append(opt)

    return compatible, incompatible
