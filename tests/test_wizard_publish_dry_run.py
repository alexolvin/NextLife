"""Wizard step 3: controlled dry-run publish check (offline, no live publish).

Tests that the dry-run reaches the Vinted skeleton (structured steps), gives an
honest BLOCKED status for marketplaces without post capability, saves the
report to attrs, and never performs a live publication (no Listing row, item
status unchanged).
"""

from decimal import Decimal


def _cleanup(session, item_code: str) -> None:
    from nextlife.storage.models import Item, ItemPhoto, Listing, Lot

    session.query(Listing).filter(Listing.item_code == item_code).delete()
    session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).delete()
    session.query(Lot).filter(Lot.item_id == item_code).delete()
    session.query(Item).filter(Item.code == item_code).delete()
    session.commit()


def _make_finalized_item(session, marketplaces=None):
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.services.wizard_finalize import finalize_draft_params
    from nextlife.services.wizard_localize import save_frame

    mps = marketplaces or ["vinted", "bazos_cz"]
    item_code = create_draft_from_selection(session, [{"title": "Шерстяной свитер"}], "", None)
    finalize_draft_params(
        session,
        item_code,
        price=Decimal("999"),
        currency="CZK",
        marketplaces=mps,
    )
    # Approved frames (the dry-run now runs on these, not on wizard_marketplaces).
    for mp in mps:
        save_frame(
            session, item_code, mp,
            {"title": "Шерстяной свитер", "price": "999", "currency": "CZK"},
            approved=True,
        )
    session.flush()
    return item_code


def test_dry_run_vinted_returns_steps_and_others_blocked():
    """Vinted + Bazos reach the dry-run (post: true); Aukro is honestly BLOCKED (post: false)."""
    from nextlife.services.wizard_publish_dry_run import publish_dry_run
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    with get_db_session() as session:
        item_code = _make_finalized_item(
            session, marketplaces=["vinted", "bazos_cz", "aukro_cz"]
        )
        report = publish_dry_run(session, item_code)
        session.commit()

        assert report["marketplaces"]["vinted"]["status"] == "DRY_RUN_OK"
        steps = report["marketplaces"]["vinted"]["steps"]
        assert [s["step"] for s in steps] == [
            "open_sell_form",
            "fill_title",
            "fill_price",
            "fill_description",
            "upload_photos",
            "submit",
            "parse_result",
        ]
        assert all(s["mode"] == "dry_run" for s in steps)

        # Bazos has post: true with a dry-run connector — reaches DRY_RUN_OK.
        assert report["marketplaces"]["bazos_cz"]["status"] == "DRY_RUN_OK"

        # Aukro has post: false in config — honest BLOCKED, never fabricated.
        assert report["marketplaces"]["aukro_cz"]["status"] == "BLOCKED"
        assert "post" in report["marketplaces"]["aukro_cz"]["detail"]

        # Report is persisted in attrs for the finalize page.
        item = session.get(Item, item_code)
        assert item.attrs["wizard_dry_run_report"]["marketplaces"]["vinted"]["status"] == "DRY_RUN_OK"
        _cleanup(session, item_code)


def test_dry_run_no_marketplaces_raises():
    """Without approved marketplaces the dry-run raises an honest InputError."""
    import pytest

    from nextlife.core.exceptions import InputError
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.services.wizard_publish_dry_run import publish_dry_run
    from nextlife.storage.db import get_db_session

    with get_db_session() as session:
        item_code = create_draft_from_selection(session, [{"title": "X"}], "", None)
        with pytest.raises(InputError):
            publish_dry_run(session, item_code)
        _cleanup(session, item_code)


def test_dry_run_missing_item_raises():
    import pytest

    from nextlife.core.exceptions import InputError
    from nextlife.services.wizard_publish_dry_run import publish_dry_run
    from nextlife.storage.db import get_db_session

    with get_db_session() as session:
        with pytest.raises(InputError):
            publish_dry_run(session, "ZZZ")


def test_dry_run_does_not_publish():
    """The dry-run never publishes: no Listing row, item status stays DRAFT."""
    from nextlife.services.wizard_publish_dry_run import publish_dry_run
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item, Listing

    with get_db_session() as session:
        item_code = _make_finalized_item(session)
        item = session.get(Item, item_code)
        status_before = item.status
        publish_dry_run(session, item_code)
        session.commit()

        listings = session.query(Listing).filter(Listing.item_code == item_code).all()
        assert listings == [], "dry-run must not create Listing rows"
        item = session.get(Item, item_code)
        assert item.status == status_before
        _cleanup(session, item_code)


def test_dry_run_oc_endpoint(monkeypatch):
    """The OC dry-run endpoint returns the structured report."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.storage.db import get_db_session

    monkeypatch.setenv("NEXTLIFE_OPENCLAW_TOKEN", "test_oc_token")

    with get_db_session() as session:
        item_code = _make_finalized_item(session)

    app = create_app()
    with TestClient(app) as c:
        resp = c.post(
            "/api/oc/wizard/publish-dry-run",
            headers={"Authorization": "Bearer test_oc_token"},
            json={"item_code": item_code},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["report"]["marketplaces"]["vinted"]["status"] == "DRY_RUN_OK"

    with get_db_session() as session:
        _cleanup(session, item_code)
