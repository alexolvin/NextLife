"""Presentation labels and tone filters for UI status and platform rendering.

TZ §3.4: Single source of truth for UI labels, tones and platform names.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import logging
from typing import Any

from nextlife.connectors.registry import CONNECTOR_MAP
from nextlife.core.status import DEAL_STATUS_LABELS_RU, ItemStatus, ListingStatus, STATUS_LABELS_RU

logger = logging.getLogger("nextlife.ui_labels")

# Canonical platform titles
PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "bazos_cz": "Bazoš.cz",
    "bazos_sk": "Bazoš.sk",
    "sbazar_cz": "Sbazar.cz",
    "aukro_cz": "Aukro.cz",
    "hyperinzerce_cz": "Hyperinzerce.cz",
    "annonce_cz": "Annonce.cz",
    "trhknih_cz": "TrhKnih.cz",
    "vinted": "Vinted",
    "kleinanzeigen_de": "Kleinanzeigen",
    "olx_pl": "OLX",
    "willhaben_at": "Willhaben",
    "facebook_marketplace": "Facebook Marketplace",
    "bazar_cz": "Bazar.cz",
    "mimibazar_cz": "Mimibazar.cz",
    "ebay": "eBay",
}

# Listing statuses specific Russian labels (overriding or supplementing STATUS_LABELS_RU)
LISTING_STATUS_LABELS_RU: dict[str, str] = {
    ListingStatus.DRAFT.value: "Черновик",
    ListingStatus.APPROVED.value: "Утверждён",
    ListingStatus.QUEUED.value: "В очереди",
    ListingStatus.PUBLISHING.value: "Публикуется",
    ListingStatus.PUBLISHED.value: "Опубликован",
    ListingStatus.UPDATING.value: "Обновляется",
    ListingStatus.EXPIRED.value: "Истёк",
    ListingStatus.FAILED.value: "Ошибка",
    ListingStatus.UNPUBLISH_FAILED.value: "Не снято",
    ListingStatus.ARCHIVED.value: "В архиве",
}

# Tones for badges: success, warning, danger, info, neutral
STATUS_TONE_MAP: dict[str, str] = {
    # Success
    ListingStatus.PUBLISHED.value: "success",
    ItemStatus.READY_FOR_SALE.value: "success",
    ItemStatus.SOLD.value: "success",
    "PAID": "success",
    "COMPLETED": "success",
    "LIVE_VERIFIED": "success",
    # Info
    ListingStatus.APPROVED.value: "info",
    ListingStatus.QUEUED.value: "info",
    ListingStatus.PUBLISHING.value: "info",
    ListingStatus.UPDATING.value: "info",
    ItemStatus.ESTIMATED.value: "info",
    ItemStatus.PHOTOGRAPHED.value: "info",
    ItemStatus.DESCRIBED.value: "info",
    ItemStatus.MEDIA_READY.value: "info",
    ItemStatus.IN_WARRANTY.value: "info",
    "DISPATCHED": "info",
    # Warning
    ListingStatus.EXPIRED.value: "warning",
    ItemStatus.RESERVED.value: "warning",
    ItemStatus.RETURNED.value: "warning",
    ItemStatus.AWAITING_APPROVAL.value: "warning",
    ItemStatus.PARTIALLY_PUBLISHED.value: "warning",
    "DISPUTED": "warning",
    # Danger
    ListingStatus.FAILED.value: "danger",
    ListingStatus.UNPUBLISH_FAILED.value: "danger",
    ItemStatus.DISPOSED.value: "danger",
    ItemStatus.DELETED.value: "danger",
    ItemStatus.CANCELLED.value: "danger",
    ItemStatus.BLOCKED.value: "danger",
    "ERROR": "danger",
    # Neutral
    ListingStatus.DRAFT.value: "neutral",
    ListingStatus.ARCHIVED.value: "neutral",
    ItemStatus.NEW.value: "neutral",
    ItemStatus.DRAFT.value: "neutral",
    ItemStatus.ARCHIVED.value: "neutral",
}


def platform_name(platform_key: str | None) -> str:
    """Return user-friendly display name for a marketplace key."""
    if not platform_key:
        return ""
    key = str(platform_key).strip().lower()
    if key in PLATFORM_DISPLAY_NAMES:
        return PLATFORM_DISPLAY_NAMES[key]
    if key in CONNECTOR_MAP:
        return key.replace("_", " ").title()
    logger.warning("Unknown marketplace platform key: %s", platform_key)
    return str(platform_key)


def status_label(status_val: str | None) -> str:
    """Return human-readable Russian label for item, listing, or deal status."""
    if not status_val:
        return ""
    raw = str(status_val).strip()

    if raw in LISTING_STATUS_LABELS_RU:
        return LISTING_STATUS_LABELS_RU[raw]
    if raw in STATUS_LABELS_RU:
        return STATUS_LABELS_RU[raw]
    if raw in DEAL_STATUS_LABELS_RU:
        return DEAL_STATUS_LABELS_RU[raw]

    logger.warning("Unknown status code for presentation: %s", raw)
    return raw


def status_tone(status_val: str | None) -> str:
    """Return badge color tone: success, warning, danger, info, neutral."""
    if not status_val:
        return "neutral"
    raw = str(status_val).strip().upper()
    return STATUS_TONE_MAP.get(raw, "neutral")


def register_ui_filters(env: Any) -> None:
    """Register all presentation filters in the Jinja environment."""
    env.filters["platform_name"] = platform_name
    env.filters["status_label"] = status_label
    env.filters["status_tone"] = status_tone
