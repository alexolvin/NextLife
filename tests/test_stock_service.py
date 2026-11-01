"""ТЗ «Склад» вместо «Ленты», этап 1.4 — тесты сервиса stock и миграции 0022.

Этап 2.4: снятие объявлений через коннекторы, публикация из мастера,
повторная продажа.
"""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from nextlife.connectors.base import CapabilityNotSupported
from nextlife.core.status import ItemStatus
from nextlife.services.draft_text import get_item_text_blocks
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.lot_delete import delete_lot
from nextlife.services.stock import (
    StockDeletionNeedsConfirmationError,
    create_stock_item,
    delete_preview,
    delete_stock_item,
    get_stock_item,
    list_stock,
    mark_sold,
    update_stock_item,
)
from nextlife.services.unpublish import unpublish_listings_now
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing, Lot, Marketplace


class _StubConnector:
    """Заглушка коннектора для тестов снятия (ТЗ §2.4)."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.calls: list[str] = []

    def unpublish(self, external_id: str) -> None:
        self.calls.append(external_id)
        if self.exc is not None:
            raise self.exc


def _add_listing(session, code: str, mp_key: str = "bazos_cz", status: str = "PUBLISHED", external_id: str = "ext-1") -> None:
    session.add(
        Listing(
            item_code=code,
            marketplace_key=mp_key,
            price=Decimal("1000"),
            currency="CZK",
            price_base=Decimal("1000"),
            status=status,
            external_id=external_id,
        )
    )


def _get_listing(session, code: str, mp_key: str = "bazos_cz") -> Listing:
    return session.scalar(select(Listing).where(Listing.item_code == code, Listing.marketplace_key == mp_key))


def _load_migration_0022():
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions" / "20261025_0022_stock_fields.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0022", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def clean_db():
    from nextlife.connectors.registry import sync_marketplaces
    from nextlife.core.config import load_config
    from tests.conftest import clean_database

    with get_db_session() as session:
        sync_marketplaces(session, load_config())
        for mp in session.execute(select(Marketplace)).scalars():
            mp.enabled = (mp.access_mode == "DIRECT")
        session.commit()
        clean_database(session)
    yield
    with get_db_session() as session:
        clean_database(session)


# --- Миграция 0022: перенос описания и язык ---------------------------------

def test_pick_description_order():
    mod = _load_migration_0022()
    assert mod._pick_description({"final_text": "A", "description": "B"}) == "A"
    assert mod._pick_description({"wizard_generated_text": {"text": "C"}, "description": "D"}) == "C"
    assert mod._pick_description({"generated_description": "E", "description": "F"}) == "E"
    assert mod._pick_description({"listing_text_cs": "G", "description": "H"}) == "G"
    assert mod._pick_description({"description": "I"}) == "I"
    # Ключ notes в перенос не входит.
    assert mod._pick_description({"notes": "J"}) is None
    assert mod._pick_description({}) is None
    assert mod._pick_description(None) is None


def test_is_cyrillic_dominant():
    mod = _load_migration_0022()
    assert mod._is_cyrillic_dominant("Привет мир") is True
    assert mod._is_cyrillic_dominant("Hello world") is False
    assert mod._is_cyrillic_dominant("") is False
    assert mod._is_cyrillic_dominant("ab") is False  # < 3 букв


# --- create_stock_item -------------------------------------------------------

def test_create_stock_item(clean_db):
    with get_db_session() as session:
        result = create_stock_item(
            session,
            {"title": "New item", "description": "Desc", "storage_location": "A1", "price": "1000", "currency": "CZK"},
        )
        assert result["title"] == "New item"
        assert result["description"] == "Desc"
        assert result["storage_location"] == "A1"
        assert result["status"] == "DRAFT"
        session.commit()


def test_create_stock_item_requires_title(clean_db):
    with get_db_session() as session:
        with pytest.raises(Exception):
            create_stock_item(session, {"title": ""})


# --- update_stock_item -------------------------------------------------------

def test_update_stock_item_syncs_lot(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        session.commit()
        update_stock_item(session, code, {"target_price": Decimal("1500"), "notes": "new notes"})
        session.commit()
        lot = session.scalar(select(Lot).where(Lot.code == code))
        assert lot.price_gross == Decimal("1500")
        assert lot.notes == "new notes"


def test_update_stock_item_draft_reset(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        item = session.get(Item, code)
        item.status = ItemStatus.AWAITING_APPROVAL.value
        session.commit()
        update_stock_item(session, code, {"description": "new desc"})
        session.commit()
        item = session.get(Item, code)
        assert item.status == ItemStatus.DRAFT.value


def test_update_stock_item_notes_no_reset(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        item = session.get(Item, code)
        item.status = ItemStatus.AWAITING_APPROVAL.value
        session.commit()
        update_stock_item(session, code, {"notes": "new notes", "storage_location": "B2"})
        session.commit()
        item = session.get(Item, code)
        assert item.status == ItemStatus.AWAITING_APPROVAL.value


def test_update_stock_item_active_listings_no_reset(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        item = session.get(Item, code)
        item.status = ItemStatus.AWAITING_APPROVAL.value
        session.add(Listing(item_code=code, marketplace_key="bazos_cz", price=Decimal("1000"), currency="CZK", price_base=Decimal("1000"), status="PUBLISHED"))
        session.commit()
        result = update_stock_item(session, code, {"description": "new desc"})
        session.commit()
        item = session.get(Item, code)
        assert item.status == ItemStatus.AWAITING_APPROVAL.value
        assert result["listings_not_updated"] is True


# --- list_stock --------------------------------------------------------------

def test_list_stock_groups(clean_db):
    with get_db_session() as session:
        c1 = create_item_and_lot(session, title="Draft item", category_key="gpu", price=Decimal("1000"), currency="CZK")
        c2 = create_item_and_lot(session, title="Published item", category_key="gpu", price=Decimal("1000"), currency="CZK")
        item2 = session.get(Item, c2["item_code"])
        item2.status = ItemStatus.PUBLISHED.value
        session.commit()
        assert len(list_stock(session, "all")) == 2
        on_sale = list_stock(session, "on_sale")
        assert [i["code"] for i in on_sale] == [c2["item_code"]]
        drafts = list_stock(session, "drafts")
        assert [i["code"] for i in drafts] == [c1["item_code"]]


def test_list_stock_search_storage(clean_db):
    with get_db_session() as session:
        c1 = create_item_and_lot(session, title="Item A", category_key="gpu", price=Decimal("1000"), currency="CZK")
        update_stock_item(session, c1["item_code"], {"storage_location": "Стеллаж 3"})
        session.commit()
        result = list_stock(session, "all", q="Стеллаж")
        assert [i["code"] for i in result] == [c1["item_code"]]


def test_list_stock_excludes_deleted_cancelled(clean_db):
    with get_db_session() as session:
        c1 = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        item = session.get(Item, c1["item_code"])
        item.status = ItemStatus.CANCELLED.value
        session.commit()
        assert list_stock(session, "all") == []


# --- get_item_text_blocks ----------------------------------------------------

def test_get_item_text_blocks_notes_not_in_description(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK", notes="operator notes")
        code = created["item_code"]
        item = session.get(Item, code)
        item.description = "Canonical description"
        session.commit()
        blocks = get_item_text_blocks(item)
        assert blocks["notes"] == "operator notes"
        assert blocks["final_text"] == "Canonical description"
        assert "operator notes" not in blocks["final_text"]


# --- mark_sold ---------------------------------------------------------------

def test_mark_sold(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        item = session.get(Item, code)
        item.status = ItemStatus.PUBLISHED.value
        session.commit()
        result = mark_sold(session, code)
        assert result["status"] == "SOLD"
        item = session.get(Item, code)
        assert item.status == "SOLD"


# --- delete_preview / delete_stock_item --------------------------------------

def test_delete_preview(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        session.commit()
        preview = delete_preview(session, code)
        assert preview["code"] == code
        assert preview["listings_count"] == 0
        assert preview["has_active_deal"] is False


def test_delete_stock_item_requires_confirm(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        session.add(Listing(item_code=code, marketplace_key="bazos_cz", price=Decimal("1000"), currency="CZK", price_base=Decimal("1000"), status="PUBLISHED"))
        session.commit()
        with pytest.raises(StockDeletionNeedsConfirmationError):
            delete_stock_item(session, code, confirm_listings=False)


def test_delete_stock_item(clean_db):
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        session.commit()
        result = delete_stock_item(session, code, confirm_listings=False)
        assert result["status"] == "DELETED"
        item = session.get(Item, code)
        assert item.status == "DELETED"
        assert all(i["code"] != code for i in list_stock(session, "all"))


# --- Этап 2.4: снятие через коннекторы ---------------------------------------

def test_unpublish_listings_now_success_archives(clean_db, monkeypatch):
    from nextlife.connectors import registry

    stub = _StubConnector()
    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: stub)
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        _add_listing(session, code, external_id="ext-1")
        session.commit()
        results = unpublish_listings_now(session, code)
        session.commit()
        assert results[0]["result"] == "archived"
        assert stub.calls == ["ext-1"]
        assert _get_listing(session, code).status == "ARCHIVED"


def test_unpublish_listings_now_capability_not_supported(clean_db, monkeypatch):
    from nextlife.connectors import registry

    stub = _StubConnector(exc=CapabilityNotSupported("unpublish not supported"))
    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: stub)
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        _add_listing(session, code, external_id="ext-1")
        session.commit()
        results = unpublish_listings_now(session, code)
        session.commit()
        assert results[0]["result"] == "failed"
        assert _get_listing(session, code).status == "UNPUBLISH_FAILED"


def test_delete_lot_no_local_archived(clean_db, monkeypatch):
    """delete_lot не ставит ARCHIVED без вызова коннектора: сбой коннектора
    оставляет UNPUBLISH_FAILED."""
    from nextlife.connectors import registry

    stub = _StubConnector(exc=CapabilityNotSupported("unpublish not supported"))
    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: stub)
    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        _add_listing(session, code, external_id="ext-1")
        session.commit()
        delete_lot(session, code)
        session.commit()
        assert _get_listing(session, code).status == "UNPUBLISH_FAILED"


def test_mark_sold_reunpublish_only_unpublish_failed(clean_db, monkeypatch):
    """mark_sold повторно не меняет статус и повторяет снятие только
    UNPUBLISH_FAILED (ARCHIVED не трогается)."""
    from nextlife.connectors import registry

    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        item = session.get(Item, code)
        item.status = ItemStatus.PUBLISHED.value
        _add_listing(session, code, mp_key="bazos_cz", external_id="ext-b")
        _add_listing(session, code, mp_key="sbazar_cz", external_id="ext-s")
        session.commit()

    # Первый раз: bazos падает, sbazar снимается.
    stub_bazos = _StubConnector(exc=CapabilityNotSupported("no"))
    stub_sbazar = _StubConnector()

    def _conn1(key, cfg):
        return stub_bazos if key == "bazos_cz" else stub_sbazar

    monkeypatch.setattr(registry, "get_connector", _conn1)
    with get_db_session() as session:
        result = mark_sold(session, code)
        session.commit()
        assert result["status"] == "SOLD"
        assert _get_listing(session, code, "bazos_cz").status == "UNPUBLISH_FAILED"
        assert _get_listing(session, code, "sbazar_cz").status == "ARCHIVED"

    # Второй раз: снимается только UNPUBLISH_FAILED (bazos).
    stub_bazos2 = _StubConnector()
    stub_sbazar2 = _StubConnector()

    def _conn2(key, cfg):
        return stub_bazos2 if key == "bazos_cz" else stub_sbazar2

    monkeypatch.setattr(registry, "get_connector", _conn2)
    with get_db_session() as session:
        result = mark_sold(session, code)
        session.commit()
        assert result["status"] == "SOLD"
        assert stub_bazos2.calls == ["ext-b"]
        assert stub_sbazar2.calls == []
        assert _get_listing(session, code, "bazos_cz").status == "ARCHIVED"


def test_wizard_publish_creates_listing_and_evidence(clean_db, monkeypatch, tmp_path):
    """Публикация из мастера с подменённой отправкой в приложение (ok=true)
    создаёт listing и файл evidence."""
    import nextlife.services.bazos_android_publish as bp
    from fastapi.testclient import TestClient

    from nextlife.api.main import create_app

    with get_db_session() as session:
        created = create_item_and_lot(session, title="T", category_key="gpu", price=Decimal("1000"), currency="CZK")
        code = created["item_code"]
        item = session.get(Item, code)
        item.attrs = {
            "wizard_frames": {
                "bazos_cz": {
                    "title": "T",
                    "description": "D",
                    "price": 1000,
                    "currency": "CZK",
                    "category": "notebook",
                    "status": "approved",
                }
            }
        }
        session.commit()

    monkeypatch.setenv("NEXTLIFE_DATA_DIR", str(tmp_path))

    def _fake_publish(*, title, description, price, category, item_code):
        ev_dir = tmp_path / "evidence" / "bazos"
        ev_dir.mkdir(parents=True, exist_ok=True)
        ev_file = ev_dir / f"{item_code}_20261025_000000.html"
        ev_file.write_text("<html>ok</html>", encoding="utf-8")
        return {
            "ok": True,
            "steps": ["done"],
            "errors": [],
            "evidence_path": str(ev_file),
            "final_url": "https://bazos.cz/inzerat/123",
            "timestamp": "2026-10-25T00:00:00Z",
        }

    monkeypatch.setattr(bp, "bazos_android_publish_listing", _fake_publish)

    client = TestClient(create_app())
    headers = {"Authorization": "Bearer test_token_secret"}
    resp = client.post(
        "/ui/lot-wizard/publish/bazos",
        data={"item_code": code},
        headers=headers,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "notice=publish_done" in resp.headers["location"]

    with get_db_session() as session:
        listing = _get_listing(session, code)
        assert listing is not None
        assert listing.marketplace_key == "bazos_cz"
        assert listing.status == "PUBLISHED"
        assert listing.price == Decimal("1000")
        assert listing.currency == "CZK"
        assert listing.publish_evidence.get("url") == "https://bazos.cz/inzerat/123"
        ev_file = listing.publish_evidence.get("evidence_file")
        assert ev_file and Path(ev_file).exists()
