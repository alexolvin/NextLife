"""Unit tests for Stage 1: UI Restructure data models, statuses and capabilities (TZ §2)."""

from datetime import UTC, datetime
from decimal import Decimal

from nextlife.connectors.registry import _registry_capabilities
from nextlife.core.config import MarketplaceConfig
from nextlife.core.status import (
    ItemStatus,
    ListingStatus,
    TRANSITIONS,
    can_transition,
    validate_transition,
)
from nextlife.core.text import build_item_search_text, normalize_search_text
from nextlife.storage.models import Item, Job, Listing, OwnerNotification


def test_item_status_enums() -> None:
    assert ItemStatus.READY_FOR_SALE == "READY_FOR_SALE"
    assert ItemStatus.DISPOSED == "DISPOSED"
    assert ItemStatus.DRAFT == "DRAFT"
    assert ItemStatus.PUBLISHED == "PUBLISHED"
    assert ItemStatus.SOLD == "SOLD"


def test_listing_status_enums() -> None:
    assert ListingStatus.DRAFT == "DRAFT"
    assert ListingStatus.APPROVED == "APPROVED"
    assert ListingStatus.QUEUED == "QUEUED"
    assert ListingStatus.PUBLISHING == "PUBLISHING"
    assert ListingStatus.PUBLISHED == "PUBLISHED"
    assert ListingStatus.UPDATING == "UPDATING"
    assert ListingStatus.EXPIRED == "EXPIRED"
    assert ListingStatus.FAILED == "FAILED"
    assert ListingStatus.UNPUBLISH_FAILED == "UNPUBLISH_FAILED"
    assert ListingStatus.ARCHIVED == "ARCHIVED"


def test_transitions_rules() -> None:
    # 1. From any active status, DELETED and DISPOSED are allowed
    for status in ItemStatus:
        if status in (ItemStatus.DELETED, ItemStatus.DISPOSED):
            continue
        assert ItemStatus.DELETED in TRANSITIONS[status], f"DELETED missing in {status}"
        assert ItemStatus.DISPOSED in TRANSITIONS[status], f"DISPOSED missing in {status}"

    assert ItemStatus.DELETED in TRANSITIONS[ItemStatus.DISPOSED]

    # 2. RETURNED -> READY_FOR_SALE
    assert can_transition(ItemStatus.RETURNED, ItemStatus.READY_FOR_SALE)

    # 3. DRAFT -> READY_FOR_SALE
    assert can_transition(ItemStatus.DRAFT, ItemStatus.READY_FOR_SALE)

    # 4. READY_FOR_SALE -> PUBLISHED and vice-versa
    assert can_transition(ItemStatus.READY_FOR_SALE, ItemStatus.PUBLISHED)
    assert can_transition(ItemStatus.PUBLISHED, ItemStatus.READY_FOR_SALE)


def test_text_normalization() -> None:
    # Strips #, case-insensitive, removes diacritics
    assert normalize_search_text("#130 ASUS Černý") == "130 asus cerny"
    assert normalize_search_text("Příliš žluťoučký kůň") == "prilis zlutoucky kun"
    assert normalize_search_text("") == ""
    assert normalize_search_text(None) == ""


def test_build_item_search_text() -> None:
    st = build_item_search_text(
        code="130",
        title="Grafická karta ASUS TUF",
        storage_location="Полка A-1",
        description="Отличное состояние",
        specs="24GB VRAM",
        notes="Коробка есть",
        attrs={"brand": "ASUS", "category": "GPU"},
    )
    assert "130" in st
    assert "#130" in st or "130" in st
    assert "graficka" in st
    assert "karta" in st
    assert "asus" in st
    assert "полка" in st


def test_registry_capabilities_canonical_flags() -> None:
    cfg = MarketplaceConfig(
        key="test_mp",
        title="Test Marketplace",
        country="CZ",
        currency="CZK",
        access_mode="DIRECT",
        base_url="https://test.local",
        auth={"type": "none"},
        capabilities={"search": True, "post": True},
        selectors={},
        rate_limits={},
    )
    caps = _registry_capabilities(cfg)
    expected_flags = [
        "search",
        "publish",
        "update",
        "unpublish",
        "relist",
        "messages_read",
        "messages_send",
        "delivery_parse",
    ]
    for flag in expected_flags:
        assert flag in caps, f"Flag {flag} missing in capabilities"
    assert caps["search"] is True
    assert caps["publish"] is True  # mapped from post
    assert caps["update"] is False
    assert caps["unpublish"] is False


def test_model_fields_instantiation() -> None:
    item = Item(
        code="999",
        kind="SELL",
        title="Тестовый предмет",
        category_key="electronics",
        status=ItemStatus.READY_FOR_SALE.value,
        warranty_months=12,
        warranty_terms="Стандартная гарантия",
        price_estimate_low=Decimal("1000.00"),
        price_estimate_median=Decimal("1200.00"),
        price_estimate_high=Decimal("1500.00"),
        price_estimate_currency="CZK",
        price_estimate_at=datetime.now(UTC),
        search_text="999 тестовый предмет",
    )
    assert item.warranty_months == 12
    assert item.price_estimate_median == Decimal("1200.00")

    listing = Listing(
        id="list-1",
        item_code="999",
        marketplace_key="bazos_cz",
        price=Decimal("1200.00"),
        currency="CZK",
        price_base=Decimal("1200.00"),
        status=ListingStatus.APPROVED.value,
        title="Заголовок объявления",
        description="Описание объявления",
        category_path="Elektronika/PC",
        category_key="pc",
        lang="cs",
        photo_ids=["photo-1", "photo-2"],
        category_auto=True,
        is_free=False,
    )
    assert listing.title == "Заголовок объявления"
    assert listing.photo_ids == ["photo-1", "photo-2"]

    job = Job(
        id="job-1",
        kind="PUBLISH_LISTING",
        state="PENDING",
        idempotency_key="key-1",
        batch_id="batch-123",
    )
    assert job.batch_id == "batch-123"

    notif = OwnerNotification(
        id="notif-1",
        kind="LISTING_PUBLISHED",
        title="Объявление размещено",
        body="Bazoš: размещено объявление для #999",
        url="/ui/listings/list-1",
    )
    assert notif.kind == "LISTING_PUBLISHED"
    assert notif.url == "/ui/listings/list-1"
