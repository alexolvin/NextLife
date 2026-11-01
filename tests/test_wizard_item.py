"""Wizard step 1 (Склад item): choose or create (ТЗ «Склад» §4.1, §4.5).

Offline — no network. The step-1 screen lists Склад items (except
SOLD/ARCHIVED/CANCELLED/DELETED); selecting or creating one goes to step 2
with item_code.
"""


def _cleanup(session, item_code: str) -> None:
    from nextlife.storage.models import Item, ItemPhoto, Lot

    session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).delete()
    session.query(Lot).filter(Lot.code == item_code).delete()
    session.query(Item).filter(Item.code == item_code).delete()
    session.commit()


def test_item_page_lists_sellable_items(monkeypatch):
    """GET /ui/lot-wizard/item lists Склад items except SOLD/ARCHIVED/CANCELLED/DELETED."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services.stock import create_stock_item
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")

    codes = {}
    with get_db_session() as session:
        codes["draft"] = create_stock_item(session, {"title": "Драфт предмет"})["code"]
        codes["sold"] = create_stock_item(session, {"title": "Проданный предмет"})["code"]
        session.get(Item, codes["sold"]).status = "SOLD"
        session.commit()

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.get("/ui/lot-wizard/item")
        assert resp.status_code == 200
        # The draft item is listed; the sold item is not.
        assert codes["draft"] in resp.text
        assert codes["sold"] not in resp.text
        # The «Новый предмет» form is present.
        assert "Новый предмет" in resp.text

    with get_db_session() as session:
        for code in codes.values():
            _cleanup(session, code)


def test_item_page_redirects_sellable_to_step2(monkeypatch):
    """Selecting an item on step 1 links to step 2 with item_code."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services.stock import create_stock_item
    from nextlife.storage.db import get_db_session

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    with get_db_session() as session:
        code = create_stock_item(session, {"title": "Предмет"})["code"]

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.get("/ui/lot-wizard/item")
        assert f"/ui/lot-wizard/text?item_code={code}" in resp.text

    with get_db_session() as session:
        _cleanup(session, code)


def test_item_new_creates_and_goes_to_step2(monkeypatch):
    """POST /ui/lot-wizard/item creates a Склад item and redirects to step 2."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.post(
            "/ui/lot-wizard/item",
            data={"title": "Новый предмет"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert location.startswith("/ui/lot-wizard/text?item_code=")
        code = location.split("item_code=")[1]

    with get_db_session() as session:
        item = session.get(Item, code)
        assert item is not None
        assert item.title == "Новый предмет"
        _cleanup(session, code)
