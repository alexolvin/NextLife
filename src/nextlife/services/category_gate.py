"""Category filter (TZ Connectors 5.7).

A lot outside ``allowed_categories`` is not queued. The reason is shown
in the stock UI and returned as NOT_ALLOWED by the operational publish.
"""

from __future__ import annotations

from typing import Any


def category_rejection(mp_cfg: Any, category_key: str | None) -> str | None:
    allowed = getattr(mp_cfg, "allowed_categories", None)
    if not allowed:
        return None
    if category_key and category_key in allowed:
        return None
    title = getattr(mp_cfg, "title", None) or getattr(mp_cfg, "key", "")
    reason = getattr(mp_cfg, "filter_reason", None) or (
        f"Категория {category_key or '—'} не входит в разрешённые для {title}: {', '.join(allowed)}."
    )
    evidence = getattr(mp_cfg, "rules_evidence", None)
    if evidence:
        return f"{reason} Доказательство: {evidence}"
    return reason


def eligible_publish_keys(config: Any, category_key: str | None, keys: list[str]) -> list[str]:
    """Drop category-rejected marketplaces and keep the preferred order."""
    priority = [
        "bazos_cz",
        "sbazar_cz",
        "aukro_cz",
        "bazar_cz",
        "mimibazar_cz",
        "allegro_cz",
        "vinted",
        "ebay",
        "facebook_marketplace",
        "bazos_sk",
        "hyperinzerce_cz",
        "annonce_cz",
        "willhaben_at",
        "olx_pl",
        "kleinanzeigen_de",
        "trhknih_cz",
    ]
    kept = []
    for key in keys:
        mp = config.marketplaces.get(key)
        if mp is None or category_rejection(mp, category_key):
            continue
        kept.append(key)
    kept.sort(key=lambda name: priority.index(name) if name in priority else 99)
    return kept


def rejections_for_item(config: Any, category_key: str | None) -> list[dict[str, str]]:
    rows = []
    for key, mp in config.marketplaces.items():
        reason = category_rejection(mp, category_key)
        if reason:
            rows.append({"marketplace": key, "title": getattr(mp, "title", key), "reason": reason})
    return rows
