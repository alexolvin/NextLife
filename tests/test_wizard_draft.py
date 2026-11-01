"""Wizard step 2: draft creation from selected results + photo linkage (offline).

Tests the Item draft creation, the stub text pulling saved data, and the photo
linkage — no external model call, no network.
"""

from io import BytesIO


def _test_png() -> bytes:
    """A valid 200x200 PNG (the media pipeline rejects 1x1 images)."""
    from PIL import Image

    img = Image.new("RGB", (200, 200), color=(120, 60, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _cleanup(session, item_code: str) -> None:
    from nextlife.storage.models import Item, ItemPhoto, Lot

    session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).delete()
    session.query(Lot).filter(Lot.code == item_code).delete()
    session.query(Item).filter(Item.code == item_code).delete()
    session.commit()


def test_create_draft_from_selection_creates_item():
    """Creating a draft from selected results creates a new Item (kind=SELL)."""
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    with get_db_session() as session:
        selected = [{"title": "iPhone 13", "price": "1000", "currency": "CZK", "url": "https://x"}]
        item_code = create_draft_from_selection(session, selected, "fast delivery", None)
        item = session.get(Item, item_code)
        assert item is not None
        assert item.kind == "SELL"
        assert item.attrs["wizard_selected"] == selected
        assert item.notes == "fast delivery"
        _cleanup(session, item_code)


def test_create_draft_with_explicit_category():
    """A declared category (clothing) is saved to the new Item."""
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    with get_db_session() as session:
        item_code = create_draft_from_selection(
            session, [{"title": "Шерстяной свитер"}], "", None, category="clothing"
        )
        item = session.get(Item, item_code)
        assert item.category_key == "clothing"
        _cleanup(session, item_code)


def test_create_draft_unknown_category_raises():
    """A category not in the reference raises InputError (honest, no silent default)."""
    import pytest

    from nextlife.core.exceptions import InputError
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session

    with get_db_session() as session:
        with pytest.raises(InputError):
            create_draft_from_selection(session, [{"title": "X"}], "", None, category="nope")


def test_create_draft_supplements_existing_item():
    """Supplementing an existing Item merges selected into attrs."""
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    with get_db_session() as session:
        # Create a base item first.
        first = create_draft_from_selection(session, [{"title": "A"}], "", None)
        # Supplement it with new selected results.
        create_draft_from_selection(session, [{"title": "B"}], "more notes", first)
        item = session.get(Item, first)
        assert item.attrs["wizard_selected"] == [{"title": "B"}]
        assert item.notes == "more notes"
        _cleanup(session, first)


def test_sixth_draft_deletes_no_item():
    """Creating a 6th DRAFT wizard draft deletes no item (ТЗ «Склад» §4.5).

    The old MAX_WIZARD_DRAFTS cap + _prune_wizard_drafts are removed; drafts are
    bounded by the Склад status groups, not a hard count.
    """
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    codes: list[str] = []
    with get_db_session() as session:
        for i in range(6):
            codes.append(create_draft_from_selection(session, [{"title": f"Draft {i}"}], "", None))
        # All 6 drafts survive — none is pruned.
        for code in codes:
            assert session.get(Item, code) is not None
        for code in codes:
            _cleanup(session, code)


def test_generate_description_pulls_saved_data(monkeypatch):
    """The generator pulls saved selected/notes from the Item into the LLM prompt."""
    from nextlife.services import llm_filter
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.services.wizard_textgen import generate_description
    from nextlife.storage.db import get_db_session

    captured: dict = {}

    def fake_call_llm(endpoint, api_key, model, prompt):
        captured["prompt"] = prompt
        return "Сгенерированный текст."

    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(llm_filter, "call_llm", fake_call_llm)

    with get_db_session() as session:
        item_code = create_draft_from_selection(
            session, [{"title": "iPhone 13"}], "fast delivery", None
        )
        gen = generate_description(item_code, None, "", session=session)
        assert gen["mode"] == "live"
        assert "iPhone 13" in captured["prompt"]
        assert "fast delivery" in captured["prompt"]
        _cleanup(session, item_code)


def test_photo_upload_links_to_item(monkeypatch):
    """Uploading a photo via the wizard route links it to the Item."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import ItemPhoto

    # Override the .env web token so the test cookie is accepted (env wins).
    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")

    with get_db_session() as session:
        item_code = create_draft_from_selection(session, [{"title": "X"}], "", None)

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.post(
            f"/ui/lot-wizard/{item_code}/photos/upload",
            files=[("files", ("t.png", BytesIO(_test_png()), "image/png"))],
            follow_redirects=False,
        )
        assert resp.status_code in (303, 302), f"expected redirect, got {resp.status_code}"

    with get_db_session() as session:
        photos = session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).all()
        assert len(photos) >= 1, "photo should be linked to the item"
        _cleanup(session, item_code)
