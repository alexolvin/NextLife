"""Unit and integration tests for listing category auto-inference, options, and navigation back to stock item.

Rule K-1: Module must strictly be <= 400 lines.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.services.listing_categories import (
    get_listing_category_options,
    get_marketplace_category_choice,
    infer_item_category,
)


def test_infer_item_category_keyword_matching():
    # GPU
    assert infer_item_category("Sapphire AMD Radeon RX 7900 XTX 24GB Nitro+") == "gpu"
    assert infer_item_category("Palit GeForce RTX 4080 GamingPro") == "gpu"
    assert infer_item_category("Видеокарта Gigabyte GTX 1660") == "gpu"

    # Laptops
    assert infer_item_category("Lenovo ThinkPad X1 Carbon Gen 9") == "laptops"
    assert infer_item_category("Apple MacBook Air 13 M2") == "laptops"
    assert infer_item_category("Ноутбук ASUS Zenbook 14") == "laptops"

    # Smartphones
    assert infer_item_category("Apple iPhone 15 Pro 128GB") == "smartphones"
    assert infer_item_category("Samsung Galaxy S24 Ultra") == "smartphones"
    assert infer_item_category("Смартфон Xiaomi Redmi Note 13") == "smartphones"

    # Photo
    assert infer_item_category("Canon EOS R6 body") == "photo"
    assert infer_item_category("Объектив Sony FE 24-70mm F2.8 GM") == "photo"

    # Bicycles
    assert infer_item_category("Гравийный велосипед Merida Silex 400") == "bicycles"
    assert infer_item_category("Horsk\u00e9 kolo Scott Scale 965") == "bicycles"

    # Furniture
    assert infer_item_category("Кожаный раскладной диван IKEA") == "furniture"
    assert infer_item_category("Офисный рабочий стол") == "furniture"

    # Books
    assert infer_item_category("Книга Гарри Поттер и философский камень") == "books"

    # Preserves explicit category
    assert infer_item_category("Нечто странное", current_category="furniture") == "furniture"

    # Fallback
    assert infer_item_category("Непонятный сувенир 123", current_category="general") == "general"


def test_get_marketplace_category_choice_bazos_and_canonical():
    # Bazos category tree mapping
    k, p = get_marketplace_category_choice("gpu", "bazos_cz")
    assert k == "pc/Grafické karty"
    assert "Grafické karty" in p

    k, p = get_marketplace_category_choice("laptops", "bazos_cz")
    assert k == "pc/Notebooky"
    assert "Notebooky" in p

    k, p = get_marketplace_category_choice("smartphones", "bazos_cz", text="Apple iPhone 14 Pro")
    assert k == "mobil/Apple"
    assert "Apple" in p

    # Other marketplace without tree
    k, p = get_marketplace_category_choice("gpu", "sbazar_cz")
    assert k == "gpu"
    assert p == "Видеокарты"


def test_get_listing_category_options():
    bazos_opts = get_listing_category_options("bazos_cz")
    assert len(bazos_opts) > 0
    # Should have PC a IT
    pc_group = next((g for g in bazos_opts if "PC a IT" in g["group"]), None)
    assert pc_group is not None
    assert any(opt["key"] == "pc/Grafické karty" for opt in pc_group["options"])

    # Non-tree marketplace
    sbazar_opts = get_listing_category_options("sbazar_cz")
    assert len(sbazar_opts) == 1
    assert any(opt["key"] == "gpu" for opt in sbazar_opts[0]["options"])


def test_listing_detail_screen_renders_back_to_item_and_category_select():
    mock_session = MagicMock()
    mock_listing = SimpleNamespace(
        id="list-123",
        item_code="130",
        marketplace_key="bazos_cz",
        external_id=None,
        external_url=None,
        price=15000,
        currency="CZK",
        status="DRAFT",
        title="Radeon RX 7900 XTX",
        description="Отличная карта",
        category_key="pc/Grafické karty",
        category_path="PC a IT / Grafické karty",
        category_auto=True,
        lang="cs",
        photo_ids=[],
        first_published_at=None,
        expires_at=None,
    )
    mock_item = SimpleNamespace(code="130", title="Radeon RX 7900 XTX", category_key="gpu")

    def _get(model, ident):
        if ident == "list-123":
            return mock_listing
        if ident == "130":
            return mock_item
        return None

    mock_session.get.side_effect = _get
    mock_session.scalars.return_value.all.return_value = []

    client = TestClient(create_app())
    client.cookies.set("nextlife_token", "test_token_secret")

    with patch("nextlife.storage.db.get_db_session") as mock_db, patch(
        "nextlife.api.routes_listings.get_db_session"
    ) as mock_db2:
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_session
        mock_ctx.__exit__.return_value = None
        mock_db.return_value = mock_ctx
        mock_db2.return_value = mock_ctx

        resp = client.get("/ui/listings/list-123?return_to=/ui/stock/130#listings")
        assert resp.status_code == 200
        html = resp.text

        # Verify Back to Item button
        assert 'data-testid="btn-back-to-item"' in html
        assert "Назад к предмету #130" in html
        assert "/ui/stock/130#listings" in html

        # Verify Category select and options
        assert 'data-testid="select-listing-category"' in html
        assert "pc/Grafické karty" in html
        assert "selected" in html

        # Verify Save and Back button
        assert 'data-testid="btn-save-and-back"' in html
        assert "Сохранить и вернуться к предмету" in html


def test_ui_save_listing_save_and_back_redirect():
    mock_session = MagicMock()
    mock_listing = SimpleNamespace(id="list-123", item_code="130")
    mock_session.get.return_value = mock_listing

    client = TestClient(create_app())
    client.cookies.set("nextlife_token", "test_token_secret")

    with patch("nextlife.storage.db.get_db_session") as mock_db, patch(
        "nextlife.api.routes_listings.get_db_session"
    ) as mock_db2, patch("nextlife.api.routes_listings.update_listing") as mock_upd:
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_session
        mock_ctx.__exit__.return_value = None
        mock_db.return_value = mock_ctx
        mock_db2.return_value = mock_ctx
        mock_upd.return_value = mock_listing

        resp = client.post(
            "/ui/listings/list-123/save",
            data={
                "title": "New Title",
                "price": "14000",
                "category_key": "pc/Grafické karty",
                "return_to": "/ui/stock/130#listings",
                "submit_action": "save_and_back",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/stock/130#listings"


def test_publish_approved_redirects_to_batch_and_renders_back_button():
    client = TestClient(create_app())
    client.cookies.set("nextlife_token", "test_token_secret")

    with patch("nextlife.storage.db.get_db_session") as mock_db, patch(
        "nextlife.api.routes_listings.get_db_session"
    ) as mock_db2, patch(
        "nextlife.api.routes_listings.publish_approved_listings", return_value="batch-abc-123"
    ):
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_session
        mock_ctx.__exit__.return_value = None
        mock_db.return_value = mock_ctx
        mock_db2.return_value = mock_ctx

        resp = client.post(
            "/ui/listings/publish-approved",
            data={"item_code": "130"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/ui/jobs/batches/batch-abc-123"

    with patch("nextlife.api.routes_jobs.get_batch_summary") as mock_sum:
        mock_sum.return_value = {
            "batch_id": "batch-abc-123",
            "item_code": "130",
            "total": 1,
            "succeeded": 1,
            "failed": 0,
            "running": 0,
            "pending": 0,
            "is_completed": True,
            "jobs": [{"id": "j1"}],
            "items": [{"state": "SUCCESS", "marketplace": "bazos_cz", "item_code": "130"}],
        }

        resp2 = client.get("/ui/jobs/batches/batch-abc-123")
        assert resp2.status_code == 200
        assert 'data-testid="btn-back-to-item-from-batch"' in resp2.text
        assert "/ui/stock/130#listings" in resp2.text

