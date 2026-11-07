"""Listings sorting by lifecycle status (§User Request 2026-11-07).

Hierarchy (top to bottom):
1. Размещенные (PUBLISHED, UPDATING, ACTIVE, PUBLISHING)
2. Готовые к размещению (APPROVED, QUEUED)
3. Ошибка размещения (FAILED, UNPUBLISH_FAILED, ERROR, or non-empty last_error_text)
4. Неразмещаемые / желтые (DRAFT, PENDING_REVIEW, NEW, etc.)
5. Снятые / архивные (ARCHIVED, EXPIRED)

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any, TypeVar

T = TypeVar("T")


def listing_sort_priority(listing: Any) -> int:
    """Return integer sorting rank (1 = top, 5 = bottom)."""
    if not listing:
        return 99

    if isinstance(listing, dict):
        status = listing.get("status")
        last_error = listing.get("last_error_text")
    else:
        status = getattr(listing, "status", None)
        last_error = getattr(listing, "last_error_text", None)

    status_str = str(status or "").strip().upper()
    has_error = bool(last_error and str(last_error).strip())

    # 1. Размещенные
    if status_str in ("PUBLISHED", "UPDATING", "ACTIVE", "PUBLISHING"):
        return 1

    # 2. Готовые к размещению
    if status_str in ("APPROVED", "QUEUED") and not has_error:
        return 2

    # 3. Ошибка размещения
    if has_error or status_str in ("FAILED", "UNPUBLISH_FAILED", "ERROR"):
        return 3

    # 5. Снятые / архивные
    if status_str in ("ARCHIVED", "EXPIRED"):
        return 5

    # 4. Неразмещаемые (желтые: DRAFT, etc.)
    return 4


def sort_listings_by_status(listings: list[T]) -> list[T]:
    """Sort listings list by status hierarchy:

    1. Размещенные
    2. Готовые к размещению
    3. Ошибка размещения
    4. Неразмещаемые (желтые)
    5. Снятые / архивные
    Secondary: marketplace_key asc, then id.
    """
    def _key(item: Any) -> tuple[int, str, str]:
        prio = listing_sort_priority(item)
        if isinstance(item, dict):
            mp = str(item.get("marketplace_key") or "")
            iid = str(item.get("id") or "")
        else:
            mp = str(getattr(item, "marketplace_key", "") or "")
            iid = str(getattr(item, "id", "") or "")
        return (prio, mp, iid)

    return sorted(listings, key=_key)
