"""Wizard step 3: finalize publish params (approval, no publish) — offline.

Tests saving price/currency/marketplaces/publish_at/repost to the Item/Lot and
reading them back, plus the OC endpoint and the web page render. No publish,
no network.
"""


def _cleanup(session, item_code: str) -> None:
    from nextlife.storage.models import Item, ItemPhoto, Lot

    session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).delete()
    session.query(Lot).filter(Lot.item_id == item_code).delete()
    session.query(Item).filter(Item.code == item_code).delete()
    session.commit()


def test_finalize_saves_params_and_marks_approved():
    """Finalize saves price/currency to price fields and the rest to attrs."""
    from decimal import Decimal

    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.services.wizard_finalize import finalize_draft_params
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item, Lot

    with get_db_session() as session:
        item_code = create_draft_from_selection(session, [{"title": "iPhone 13"}], "", None)
        finalize_draft_params(
            session,
            item_code,
            price=Decimal("4999"),
            currency="CZK",
            marketplaces=["vinted", "bazos_cz", "aukro_cz"],
            publish_at="2026-10-20T12:00",
            repost=True,
        )
        session.commit()
        item = session.get(Item, item_code)
        lot = session.query(Lot).filter(Lot.item_id == item_code).first()
        assert item.target_price == Decimal("4999")
        assert item.target_currency == "CZK"
        assert lot is not None
        assert lot.price_gross == Decimal("4999")
        assert lot.currency == "CZK"
        assert item.attrs["wizard_marketplaces"] == ["vinted", "bazos_cz", "aukro_cz"]
        assert item.attrs["wizard_publish_at"] == "2026-10-20T12:00"
        assert item.attrs["wizard_repost"] is True
        assert item.attrs["wizard_status"] == "params_approved"
        _cleanup(session, item_code)


def test_finalize_missing_item_raises():
    """Finalizing an unknown item_code raises InputError."""
    import pytest

    from nextlife.core.exceptions import InputError
    from nextlife.services.wizard_finalize import finalize_draft_params
    from nextlife.storage.db import get_db_session

    with get_db_session() as session:
        with pytest.raises(InputError):
            finalize_draft_params(session, "ZZZ")


def test_finalize_oc_endpoint(monkeypatch):
    """The OC finalize endpoint saves the params and marks the draft approved."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    # Override the .env openclaw token so the test bearer is accepted (env wins).
    monkeypatch.setenv("NEXTLIFE_OPENCLAW_TOKEN", "test_oc_token")

    with get_db_session() as session:
        item_code = create_draft_from_selection(session, [{"title": "X"}], "", None)

    app = create_app()
    with TestClient(app) as c:
        resp = c.post(
            "/api/oc/wizard/finalize",
            headers={"Authorization": "Bearer test_oc_token"},
            json={
                "item_code": item_code,
                "price": 1234.5,
                "currency": "CZK",
                "marketplaces": ["vinted"],
                "publish_at": "2026-10-20T12:00",
                "repost": False,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["wizard_status"] == "params_approved"

    with get_db_session() as session:
        item = session.get(Item, item_code)
        assert item.attrs["wizard_status"] == "params_approved"
        assert item.attrs["wizard_marketplaces"] == ["vinted"]
        _cleanup(session, item_code)


def test_finalize_page_renders(monkeypatch):
    """The finalize page renders the platform frames feed."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session

    # Override the .env web token so the test cookie is accepted (env wins).
    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")

    with get_db_session() as session:
        item_code = create_draft_from_selection(session, [{"title": "X"}], "", None)

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.get(f"/ui/lot-wizard/finalize?item_code={item_code}")
        assert resp.status_code == 200
        assert "Утверждение площадок" in resp.text

    with get_db_session() as session:
        _cleanup(session, item_code)
