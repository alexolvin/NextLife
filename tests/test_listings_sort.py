"""Tests for listings lifecycle status sorting (§User Request 2026-11-07).

Hierarchy:
1. Размещенные (PUBLISHED, UPDATING, ACTIVE, PUBLISHING)
2. Готовые к размещению (APPROVED, QUEUED)
3. Ошибка размещения (FAILED, UNPUBLISH_FAILED, ERROR, last_error_text)
4. Неразмещаемые / желтые (DRAFT, NEW, etc.)
5. Снятые / архивные (ARCHIVED, EXPIRED)
"""

from __future__ import annotations

import pytest
from nextlife.services.listings_sort import listing_sort_priority, sort_listings_by_status


def test_listing_sort_priority_hierarchy():
    # 1. Размещенные
    assert listing_sort_priority({"status": "PUBLISHED"}) == 1
    assert listing_sort_priority({"status": "UPDATING"}) == 1
    assert listing_sort_priority({"status": "ACTIVE"}) == 1
    assert listing_sort_priority({"status": "PUBLISHING"}) == 1

    # 2. Готовые к размещению
    assert listing_sort_priority({"status": "APPROVED"}) == 2
    assert listing_sort_priority({"status": "QUEUED"}) == 2

    # 3. Ошибка размещения
    assert listing_sort_priority({"status": "FAILED"}) == 3
    assert listing_sort_priority({"status": "UNPUBLISH_FAILED"}) == 3
    assert listing_sort_priority({"status": "ERROR"}) == 3
    assert listing_sort_priority({"status": "APPROVED", "last_error_text": "Connection timed out"}) == 3
    assert listing_sort_priority({"status": "DRAFT", "last_error_text": "Error posting"}) == 3

    # 4. Неразмещаемые (желтые)
    assert listing_sort_priority({"status": "DRAFT"}) == 4
    assert listing_sort_priority({"status": "NEW"}) == 4
    assert listing_sort_priority({"status": "PENDING_REVIEW"}) == 4

    # 5. Снятые / архивные
    assert listing_sort_priority({"status": "ARCHIVED"}) == 5
    assert listing_sort_priority({"status": "EXPIRED"}) == 5


def test_sort_listings_by_status_ordering():
    raw_listings = [
        {"id": 1, "marketplace_key": "bazos_cz", "status": "DRAFT"},
        {"id": 2, "marketplace_key": "aukro_cz", "status": "FAILED", "last_error_text": "Blocked"},
        {"id": 3, "marketplace_key": "sbazar_cz", "status": "APPROVED"},
        {"id": 4, "marketplace_key": "bazos_sk", "status": "PUBLISHED"},
        {"id": 5, "marketplace_key": "ebay", "status": "ARCHIVED"},
        {"id": 6, "marketplace_key": "allegro_cz", "status": "DRAFT"},
    ]

    sorted_list = sort_listings_by_status(raw_listings)
    statuses = [item["status"] for item in sorted_list]
    mps = [item["marketplace_key"] for item in sorted_list]

    # Order must strictly be:
    # 1. PUBLISHED (bazos_sk)
    # 2. APPROVED (sbazar_cz)
    # 3. FAILED (aukro_cz)
    # 4. DRAFT (allegro_cz, bazos_cz)
    # 5. ARCHIVED (ebay)
    assert statuses[0] == "PUBLISHED"
    assert mps[0] == "bazos_sk"

    assert statuses[1] == "APPROVED"
    assert mps[1] == "sbazar_cz"

    assert statuses[2] == "FAILED"
    assert mps[2] == "aukro_cz"

    assert statuses[3] == "DRAFT"
    assert statuses[4] == "DRAFT"
    assert mps[3] == "allegro_cz"
    assert mps[4] == "bazos_cz"

    assert statuses[5] == "ARCHIVED"
    assert mps[5] == "ebay"
