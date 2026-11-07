"""Test suite for Stage 7: listings preparation, management and relisting (TZ §5.6, §6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nextlife.core.status import ListingStatus
from nextlife.services.listings import (
    ListingValidationError,
    approve_listing,
    check_and_enqueue_expired_relistings,
    relist_listing_now,
    set_listing_free,
    unapprove_listing,
    unpublish_listing,
)
from nextlife.storage.models import Item, Listing


def test_approve_listing_validation_criteria():
    session = MagicMock()
    mock_listing = Listing(
        id="lst-1",
        item_code="ITM-1",
        marketplace_key="bazos_cz",
        status=ListingStatus.DRAFT.value,
        title="",  # missing title!
        description="",
        category_key="",
        price=None,
        photo_ids=[],
    )
    session.get.return_value = mock_listing

    # Fails when required fields missing
    with pytest.raises(ListingValidationError) as exc:
        approve_listing(session, "lst-1")
    assert "Не заполнено" in str(exc.value)

    # Fill all required fields
    mock_listing.title = "Телефон Samsung Galaxy"
    mock_listing.description = "В отличном состоянии"
    mock_listing.category_key = "123"
    mock_listing.price = Decimal("2500")
    mock_listing.photo_ids = ["p-1"]

    approved = approve_listing(session, "lst-1")
    assert approved.status == ListingStatus.APPROVED.value

    # Revert via unapprove
    unapproved = unapprove_listing(session, "lst-1")
    assert unapproved.status == ListingStatus.DRAFT.value


def test_set_listing_free():
    session = MagicMock()
    mock_listing = Listing(
        id="lst-2",
        item_code="ITM-2",
        marketplace_key="bazos_cz",
        status=ListingStatus.DRAFT.value,
        title="Старый монитор",
        description="Рабочий",
        price=Decimal("500"),
        is_free=False,
    )
    session.get.return_value = mock_listing

    res = set_listing_free(session, "lst-2", is_free=True)
    assert res.is_free is True
    assert res.price == Decimal("0")
    assert "Daruji za odvoz" in res.description


def test_unpublish_and_relist_capabilities_check():
    session = MagicMock()
    mock_listing = Listing(
        id="lst-3",
        item_code="ITM-3",
        marketplace_key="fake_unsupported_mp",
        status=ListingStatus.PUBLISHED.value,
        external_url="https://example.com/ad",
    )
    session.get.return_value = mock_listing

    with patch("nextlife.services.listings.get_marketplace_capabilities", return_value={"unpublish": False, "relist": False}):
        unpub_res = unpublish_listing(session, "lst-3")
        assert not unpub_res["supported"]
        assert "Снятие не поддерживается площадкой: снимите вручную" in unpub_res["message"]

        relist_res = relist_listing_now(session, "lst-3")
        assert not relist_res["supported"]
        assert "Перевыставление не поддерживается площадкой" in relist_res["message"]


def test_hourly_expired_relisting_task():
    session = MagicMock()
    now = datetime.now(UTC)
    expired_listing = Listing(
        id="lst-expired",
        item_code="ITM-4",
        marketplace_key="bazos_cz",
        status=ListingStatus.PUBLISHED.value,
        auto_relist=True,
        expires_at=now - timedelta(hours=2),
    )
    session.scalars.return_value.all.return_value = [expired_listing]

    with patch("nextlife.services.listings.get_marketplace_capabilities", return_value={"relist": True}):
        res = check_and_enqueue_expired_relistings(session)
        assert res["relisted"] == 1
        assert res["expired"] == 0
        session.add.assert_called()


def test_listings_screen_routes():
    from nextlife.api.main import create_app
    from nextlife.api.routes import check_auth

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    with patch("nextlife.api.routes_listings.get_db_session") as mock_session_ctx:
        session = MagicMock()
        mock_counts = {
            "published": 5, "errors": 1, "approved": 2, "drafts": 3, "expiring": 0, "archived": 1
        }
        session.scalars.return_value.all.return_value = []
        mock_session_ctx.return_value.__enter__.return_value = session

        with patch("nextlife.api.routes_listings.get_listings_counts", return_value=mock_counts):
            resp = client.get("/ui/listings")
            assert resp.status_code == 200
            assert "Объявления" in resp.text
            assert 'data-testid="listings-screen"' in resp.text
            assert 'data-testid="chip-all"' in resp.text
            assert 'data-testid="chip-errors"' in resp.text
            assert 'data-testid="chip-approved"' in resp.text
            assert 'data-testid="btn-publish-approved"' in resp.text


def test_listing_detail_screen_route():
    from nextlife.api.main import create_app
    from nextlife.api.routes import check_auth

    app = create_app()
    app.dependency_overrides[check_auth] = lambda: True
    client = TestClient(app)

    with patch("nextlife.api.routes_listings.get_db_session") as mock_session_ctx:
        session = MagicMock()
        mock_listing = Listing(
            id="lst-test-id",
            item_code="ITM-55",
            marketplace_key="bazos_cz",
            status=ListingStatus.DRAFT.value,
            title="Тестовое объявление",
            description="Тестовое описание",
            price=Decimal("1200"),
            currency="CZK",
            category_auto=True,
            photo_ids=[],
        )
        mock_item = Item(code="ITM-55", title="Предмет 55")
        session.get.side_effect = lambda model, pk: mock_listing if model == Listing else mock_item
        session.scalars.return_value.all.return_value = []
        mock_session_ctx.return_value.__enter__.return_value = session

        with patch("nextlife.api.routes_listings.get_marketplace_capabilities", return_value={"unpublish": False, "relist": True}):
            resp = client.get("/ui/listings/lst-test-id")
            assert resp.status_code == 200
            assert "Тестовое объявление" in resp.text
            assert 'data-testid="listing-detail-screen"' in resp.text
            assert 'data-testid="input-listing-title"' in resp.text
            assert 'data-testid="btn-approve"' in resp.text
            assert "Снятие не поддерживается площадкой: снимите вручную" in resp.text


def test_no_forbidden_words_in_stage_7_templates():
    forbidden = ["лот", "мастер создания лота", "мастер лота", "dry-run", "фрейм"]
    files = [
        Path("templates/web/listings.html"),
        Path("templates/web/listing_detail.html"),
    ]
    for f in files:
        if f.exists():
            content = f.read_text(encoding="utf-8").lower()
            for word in forbidden:
                assert word not in content, f"Forbidden word '{word}' found in {f}"
