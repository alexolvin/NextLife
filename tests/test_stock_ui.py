"""ТЗ «Склад» вместо «Ленты», этап 3.7 — тесты интерфейса «Склад»."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from nextlife.api.main import create_app
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing, Marketplace


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def clean_db():
    from tests.conftest import clean_database

    from nextlife.connectors.registry import sync_marketplaces
    from nextlife.core.config import load_config

    with get_db_session() as session:
        sync_marketplaces(session, load_config())
        for mp in session.execute(select(Marketplace)).scalars():
            mp.enabled = (mp.access_mode == "DIRECT")
        session.commit()
        clean_database(session)
    yield
    with get_db_session() as session:
        clean_database(session)


def _auth() -> dict[str, str]:
    from nextlife.core.config import load_config
    from nextlife.core.secrets import resolve_secret
    tok = resolve_secret(load_config().app.auth.web_token_secret_ref) or "test_token_secret"
    return {"Authorization": f"Bearer {tok}", "Cookie": f"nextlife_token={tok}"}


# --- Редиректы ---------------------------------------------------------------


def test_ui_items_redirects_to_stock(client, clean_db):
    r = client.get("/ui/items", headers=_auth(), follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"].startswith("/ui/stock")

    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        session.commit()
    r2 = client.get(f"/ui/items/{code}", headers=_auth(), follow_redirects=False)
    assert r2.status_code == 301
    assert r2.headers["location"] == f"/ui/stock/{code}"


# --- Список /ui/stock --------------------------------------------------------


def test_stock_list_shows_storage_location(client, clean_db):
    from nextlife.services.stock import update_stock_item

    with get_db_session() as session:
        c = create_item_and_lot(session, title="Видеокарта RTX", category_key="gpu", price=Decimal("5000"), currency="CZK")
        code = c["item_code"]
        update_stock_item(session, code, {"storage_location": "Стеллаж 3"})
        session.commit()
    r = client.get("/ui/stock", headers=_auth())
    assert r.status_code == 200
    assert "Видеокарта RTX" in r.text
    assert "Стеллаж 3" in r.text
    assert f"/ui/stock/{code}" in r.text


def test_stock_list_empty_state(client, clean_db):
    r = client.get("/ui/stock", headers=_auth())
    assert r.status_code == 200
    assert "Склад пуст" in r.text


# --- Создание /ui/stock/new + POST /api/stock --------------------------------


def test_stock_new_screen_renders(client, clean_db):
    r = client.get("/ui/stock/new", headers=_auth())
    assert r.status_code == 200
    assert 'data-testid="stock-form"' in r.text
    assert 'data-testid="stock-title"' in r.text


def test_stock_create_via_api(client, clean_db):
    r = client.post(
        "/api/stock",
        headers=_auth(),
        data={
            "title": "Новый предмет",
            "description_lang": "ru",
            "target_price": "1200",
            "target_currency": "CZK",
            "storage_location": "Полка 1",
            "description": "Описание",
            "usage": "Использование",
            "specs": "Характеристики",
            "notes": "Заметки",
            "shipping_policy": "BUYER_PAYS",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/ui/stock/")
    code = r.headers["location"].split("/ui/stock/")[1].split("?")[0].split("#")[0]

    with get_db_session() as session:
        item = session.get(Item, code)
        assert item is not None
        assert item.title == "Новый предмет"
        assert item.storage_location == "Полка 1"
        assert item.description == "Описание"
        assert item.usage == "Использование"
        assert item.specs == "Характеристики"
        assert item.notes == "Заметки"
        assert item.target_price == Decimal("1200")
        assert item.target_currency == "CZK"


def test_stock_create_requires_title(client, clean_db):
    r = client.post(
        "/api/stock",
        headers=_auth(),
        data={"title": ""},
        follow_redirects=False,
    )
    assert r.status_code == 422


# --- Правка PATCH /api/stock/{code} ------------------------------------------


def test_stock_update_each_field(client, clean_db):
    with get_db_session() as session:
        c = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = c["item_code"]
        session.commit()
    r = client.post(
        f"/api/stock/{code}",
        headers=_auth(),
        data={
            "title": "Изменённый",
            "description_lang": "cs",
            "target_price": "2000",
            "target_currency": "EUR",
            "storage_location": "Стеллаж 9",
            "description": "Новое описание",
            "usage": "Новое использование",
            "specs": "Новые характеристики",
            "notes": "Новые заметки",
            "shipping_policy": "SELLER_PAYS",
            "weight_g": "500",
            "packed_weight_g": "700",
            "dim_l_mm": "100",
            "dim_w_mm": "50",
            "dim_h_mm": "20",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with get_db_session() as session:
        item = session.get(Item, code)
        assert item.title == "Изменённый"
        assert item.description_lang == "cs"
        assert item.target_price == Decimal("2000")
        assert item.target_currency == "EUR"
        assert item.storage_location == "Стеллаж 9"
        assert item.description == "Новое описание"
        assert item.usage == "Новое использование"
        assert item.specs == "Новые характеристики"
        assert item.notes == "Новые заметки"
        assert item.weight_g == 500
        assert item.packed_weight_g == 700
        assert item.dim_l_mm == 100
        assert item.dim_w_mm == 50
        assert item.dim_h_mm == 20


# --- Экран предмета /ui/stock/{code} -----------------------------------------


def test_stock_item_screen_renders(client, clean_db):
    with get_db_session() as session:
        c = create_item_and_lot(session, title="Предмет экрана", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = c["item_code"]
        session.commit()
    r = client.get(f"/ui/stock/{code}", headers=_auth())
    assert r.status_code == 200
    assert "Предмет экрана" in r.text
    assert 'data-testid="stock-form"' in r.text
    assert 'data-testid="stock-listings"' in r.text
    assert 'data-testid="btn-mark-sold"' in r.text
    assert 'data-testid="btn-delete-item"' in r.text


def test_stock_item_screen_renders_with_listings(client, clean_db):
    """Проверка рендера карточки предмета с листингами (защита от NameError: ListingStatus)."""
    with get_db_session() as session:
        c = create_item_and_lot(session, title="Предмет с листингом", category_key="gpu", price=Decimal("1500"), currency="CZK")
        code = c["item_code"]
        session.add(
            Listing(
                item_code=code,
                marketplace_key="bazos_cz",
                price=Decimal("1500"),
                currency="CZK",
                price_base=Decimal("1500"),
                status="APPROVED",
            )
        )
        session.commit()
    r = client.get(f"/ui/stock/{code}", headers=_auth())
    assert r.status_code == 200
    assert "Предмет с листингом" in r.text
    assert "bazos_cz" in r.text


def test_stock_item_screen_404(client, clean_db):
    r = client.get("/ui/stock/999", headers=_auth())
    assert r.status_code == 404


# --- Удаление ----------------------------------------------------------------


def test_stock_delete_409_without_confirm(client, clean_db):
    with get_db_session() as session:
        c = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = c["item_code"]
        session.add(
            Listing(
                item_code=code,
                marketplace_key="bazos_cz",
                price=Decimal("1000"),
                currency="CZK",
                price_base=Decimal("1000"),
                status="PUBLISHED",
            )
        )
        session.commit()
    r = client.delete(f"/api/stock/{code}", headers=_auth())
    assert r.status_code == 409
    assert "delete_preview" in r.json()

    with get_db_session() as session:
        item = session.get(Item, code)
        assert item.status != "DELETED"


def test_stock_delete_with_confirm(client, clean_db):
    with get_db_session() as session:
        c = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = c["item_code"]
        session.add(
            Listing(
                item_code=code,
                marketplace_key="bazos_cz",
                price=Decimal("1000"),
                currency="CZK",
                price_base=Decimal("1000"),
                status="PUBLISHED",
            )
        )
        session.commit()
    r = client.delete(f"/api/stock/{code}?confirm_listings=true", headers=_auth())
    assert r.status_code == 200
    assert r.json()["status"] == "DELETED"

    with get_db_session() as session:
        item = session.get(Item, code)
        assert item.status == "DELETED"

    # Предмет исчез из списка.
    r2 = client.get("/ui/stock", headers=_auth())
    assert f"/ui/stock/{code}" not in r2.text


def test_stock_delete_preview_endpoint(client, clean_db):
    with get_db_session() as session:
        c = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = c["item_code"]
        session.commit()
    r = client.get(f"/api/stock/{code}/delete-preview", headers=_auth())
    assert r.status_code == 200
    assert r.json()["code"] == code
    assert r.json()["listings_count"] == 0


# --- Исследования и поиск ----------------------------------------------------


def test_ui_item_research_get_and_post(client, clean_db):
    """GET и POST на /ui/items/{code}/research и /ui/stock/{code}/research возвращают 303 (не 405)."""
    with get_db_session() as session:
        c = create_item_and_lot(session, title="Видеокарта тест", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = c["item_code"]
        session.commit()

    # GET /ui/items/{code}/research
    r1 = client.get(f"/ui/items/{code}/research", headers=_auth(), follow_redirects=False)
    assert r1.status_code == 303
    assert f"/ui/stock/{code}" in r1.headers["location"]

    # GET /ui/stock/{code}/research
    r2 = client.get(f"/ui/stock/{code}/research", headers=_auth(), follow_redirects=False)
    assert r2.status_code == 303
    assert f"/ui/stock/{code}" in r2.headers["location"]

    # POST /ui/stock/{code}/research с указанием next
    r3 = client.post(
        f"/ui/stock/{code}/research",
        data={"next": f"/ui/stock/{code}#price"},
        headers=_auth(),
        follow_redirects=False,
    )
    assert r3.status_code == 303
    assert f"/ui/stock/{code}" in r3.headers["location"]
    assert "#price" in r3.headers["location"]


def test_ui_search_renders_results(client, clean_db):
    """Поиск /ui/search?q=... корректно рендерит страницу без ошибки dict.items."""
    with get_db_session() as session:
        create_item_and_lot(session, title="Тестовый поиск GPU", category_key="gpu", price=Decimal("1000"), currency="CZK")
        session.commit()

    r = client.get("/ui/search?q=поиск", headers=_auth())
    assert r.status_code == 200
    assert "Тестовый поиск GPU" in r.text


def test_ui_listing_detail_screen_renders(client, clean_db):
    """Экран детальной карточки объявления /ui/listings/{id} рендерится со статусом 200."""
    with get_db_session() as session:
        c = create_item_and_lot(session, title="Предмет листинга", category_key="gpu", price=Decimal("2000"), currency="CZK")
        code = c["item_code"]
        listing = Listing(
            item_code=code,
            marketplace_key="bazos_cz",
            price=Decimal("2000"),
            currency="CZK",
            price_base=Decimal("2000"),
            status="PUBLISHED",
            title="Заголовок объявления",
            description="Описание объявления",
        )
        session.add(listing)
        session.commit()
        listing_id = listing.id

    r = client.get(f"/ui/listings/{listing_id}", headers=_auth())
    assert r.status_code == 200
    assert "bazos_cz" in r.text
    assert "Заголовок объявления" in r.text

