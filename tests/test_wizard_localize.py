"""Wizard step 3: per-marketplace frame localization (offline, LLM mocked).

Tests the localization service (LLM translation + category, default price,
frame save/approve, approved-marketplaces) — no external model call, no network.
"""


def _cleanup(session, item_code: str) -> None:
    from nextlife.storage.models import Item, ItemPhoto, Lot

    session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).delete()
    session.query(Lot).filter(Lot.code == item_code).delete()
    session.query(Item).filter(Item.code == item_code).delete()
    session.commit()


def _make_item(session) -> str:
    from nextlife.services.wizard_draft import create_draft_from_selection

    return create_draft_from_selection(
        session,
        [{"title": "iPhone 13", "price": "12000", "currency": "CZK", "url": "https://x"}],
        "fast delivery",
        None,
    )


def test_localize_listing_text_with_llm(monkeypatch):
    """The LLM translates the title/description and returns the platform category."""
    from nextlife.core.config import load_config
    from nextlife.services import llm_filter
    from nextlife.services.wizard_localize import localize_listing_text

    captured: dict = {}

    def fake_call_llm(endpoint, api_key, model, prompt):
        captured["prompt"] = prompt
        return '{"title": "iPhone 13", "description": "Rychlé doručení", "category": "Telefony"}'

    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(llm_filter, "call_llm", fake_call_llm)

    cfg = load_config()
    mp_cfg = cfg.marketplaces["bazos_cz"]
    out = localize_listing_text("Айфон 13", "Быстрая доставка", mp_cfg, "electronics", get_llm(monkeypatch))
    assert out["title"] == "iPhone 13"
    assert out["category"] == "Telefony"
    assert "чешский" in captured["prompt"]


def test_localize_listing_text_fallback_no_llm(monkeypatch):
    """Without an LLM endpoint the original text + global category are returned."""
    from nextlife.core.config import load_config
    from nextlife.services import llm_filter
    from nextlife.services.wizard_localize import localize_listing_text

    monkeypatch.setattr(llm_filter, "get_llm_config", lambda: {"endpoint": ""})
    cfg = load_config()
    mp_cfg = cfg.marketplaces["bazos_cz"]
    out = localize_listing_text("Айфон 13", "Быстрая доставка", mp_cfg, "electronics", get_llm(monkeypatch))
    assert out == {"title": "Айфон 13", "description": "Быстрая доставка", "category": "electronics"}


def test_resolve_category_for_marketplace():
    """resolve_category_for_marketplace resolves bare rubric, path, and unknown categories."""
    from nextlife.core.config import load_config
    from nextlife.services.wizard_localize import resolve_category_for_marketplace

    cfg = load_config()
    bazos_cfg = cfg.marketplaces["bazos_cz"]

    # Bare rubric in tree
    r1 = resolve_category_for_marketplace("Grafické karty", bazos_cfg)
    assert r1["canonical"] == "pc/Grafické karty"
    assert r1["display_path"] == "PC a IT / Grafické karty"
    assert r1["top"] == "pc"
    assert r1["sub"] == "Grafické karty"

    # Full path in tree
    r2 = resolve_category_for_marketplace("pc/Grafické karty", bazos_cfg)
    assert r2["canonical"] == "pc/Grafické karty"
    assert r2["display_path"] == "PC a IT / Grafické karty"

    # Another section
    r3 = resolve_category_for_marketplace("mobil/Apple", bazos_cfg)
    assert r3["canonical"] == "mobil/Apple"
    assert r3["display_path"] == "Mobilní telefony / Apple"

    # Marketplace without category_tree
    aukro_cfg = cfg.marketplaces["aukro_cz"]
    r4 = resolve_category_for_marketplace("custom_cat", aukro_cfg)
    assert r4["canonical"] == "custom_cat"
    assert r4["display_path"] == "custom_cat"


def test_save_frame_and_approved(monkeypatch):
    """Saving a frame stores it; approved_marketplaces returns only approved ones."""
    from nextlife.services.wizard_localize import approved_marketplaces, save_frame
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    with get_db_session() as session:
        code = _make_item(session)
        save_frame(session, code, "bazos_cz", {"title": "A", "status": "localized"}, approved=False)
        save_frame(session, code, "sbazar_cz", {"title": "B", "status": "localized"}, approved=True)
        session.commit()
        item = session.get(Item, code)
        frames = item.attrs["wizard_frames"]
        assert frames["bazos_cz"]["status"] == "localized"
        assert frames["sbazar_cz"]["status"] == "approved"
        assert approved_marketplaces(item) == ["sbazar_cz"]
        _cleanup(session, code)


def test_frame_price_from_target_and_reference(monkeypatch):
    """Frame price: the item's target price, else the median reference price
    (same-currency references need no FX). Returns (price, rate_date)."""
    from datetime import date

    from nextlife.services.wizard_localize import _frame_price
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    with get_db_session() as session:
        code = _make_item(session)
        item = session.get(Item, code)
        # No target price yet -> the (single same-currency) reference's price.
        assert _frame_price(item, "CZK", session) == ("12000", None)
        # With a target price (same currency) -> the target price wins, rate
        # date is today (no FX conversion).
        from decimal import Decimal

        item.target_price = Decimal("9999")
        item.target_currency = "CZK"
        session.flush()
        price, rate_date = _frame_price(item, "CZK", session)
        assert price == "9999"
        assert rate_date == date.today()
        _cleanup(session, code)


def test_frame_price_converts_references_via_eur_base(monkeypatch):
    """Reference prices in CZK/EUR/PLN are converted to the platform currency
    (CZK) via the EUR base and the median is taken (rate_date None on fallback)."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from nextlife.services.wizard_localize import _frame_price
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import FxRate, Item

    # 1 EUR = 24.7 CZK, 1 EUR = 4.30 PLN.
    with get_db_session() as session:
        for cur, rate in (("CZK", "24.7"), ("PLN", "4.30")):
            session.merge(
                FxRate(
                    rate_date=date.today(),
                    currency=cur,
                    rate_to_base=Decimal(rate),
                    source="ECB",
                    fetched_at=datetime.now(UTC),
                )
            )
        # References: 24000 CZK, 900 EUR, 3870 PLN (all ~930 EUR).
        code = create_draft_from_selection(
            session,
            [
                {"title": "A", "price": "24000", "currency": "CZK"},
                {"title": "B", "price": "900", "currency": "EUR"},
                {"title": "C", "price": "3870", "currency": "PLN"},
            ],
            "",
            None,
        )
        item = session.get(Item, code)
        # 24000 CZK -> 24000; 900 EUR -> 900*24.7 = 22230 CZK;
        # 3870 PLN -> 3870/4.30 = 900 EUR -> 900*24.7 = 22230 CZK.
        # Median of [22230, 22230, 24000] = 22230.
        assert _frame_price(item, "CZK", session) == ("22230", None)
        session.commit()
        _cleanup(session, code)


def test_localize_frame_builds(monkeypatch):
    """localize_frame assembles the localized frame with default price/currency."""
    from nextlife.core.config import load_config
    from nextlife.services import llm_filter
    from nextlife.services.wizard_localize import localize_frame
    from nextlife.storage.db import get_db_session

    def fake_call_llm(endpoint, api_key, model, prompt):
        if "Сформируй объявление" in prompt:
            return '{"title": "iPhone 13", "description": "Rychlé doručení", "category": "Telefony"}'
        return '{"title": "Айфон 13", "description": "Быстрая доставка"}'

    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(llm_filter, "call_llm", fake_call_llm)

    photos = [{"id": "p1", "display_id": "p1", "url": "/media/p1.jpg", "broken": False}]
    cfg = load_config()
    with get_db_session() as session:
        code = _make_item(session)
        frame = localize_frame(session, code, "bazos_cz", photos, cfg)
        assert frame["title"] == "iPhone 13"
        assert frame["category"] == "Telefony"
        assert frame["currency"] == "CZK"
        assert frame["price"] == "12000"
        assert frame["main_photo"] == "p1"
        assert frame["status"] == "localized"
        _cleanup(session, code)


def test_frame_source_is_item_fields_not_notes(monkeypatch):
    """localize_frame source = item.description + usage + specs (NOT notes)
    (ТЗ «Склад» §4.3.1, §4.3.4)."""
    from nextlife.core.config import load_config
    from nextlife.services import llm_filter
    from nextlife.services.wizard_localize import localize_frame
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    captured: dict = {}

    def fake_call_llm(endpoint, api_key, model, prompt):
        captured["prompt"] = prompt
        return '{"title": "T", "description": "D", "category": "C"}'

    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(llm_filter, "call_llm", fake_call_llm)

    photos = [{"id": "p1", "display_id": "p1", "url": "/media/p1.jpg", "broken": False}]
    cfg = load_config()
    with get_db_session() as session:
        code = _make_item(session)
        item = session.get(Item, code)
        item.description = "Описание предмета."
        item.usage = "Используется для работы."
        item.specs = "Модель: X\nОбъём: 16GB"
        item.notes = "СЕКРЕТНАЯ ЗАМЕТКА"
        session.flush()
        localize_frame(session, code, "bazos_cz", photos, cfg)
        # The prompt carries description + usage + specs, but NOT notes.
        assert "Описание предмета." in captured["prompt"]
        assert "Используется для работы." in captured["prompt"]
        assert "Модель: X" in captured["prompt"]
        assert "СЕКРЕТНАЯ ЗАМЕТКА" not in captured["prompt"]
        _cleanup(session, code)


def test_frame_price_converts_target_currency(monkeypatch):
    """Frame price = item.target_price converted to the platform currency via
    the ECB rate; the frame stores the rate date (ТЗ «Склад» §4.3.3)."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from nextlife.core.config import load_config
    from nextlife.services import llm_filter
    from nextlife.services.wizard_localize import localize_frame
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import FxRate, Item

    monkeypatch.setattr(llm_filter, "get_llm_config", lambda: {"endpoint": ""})
    photos = [{"id": "p1", "display_id": "p1", "url": "/media/p1.jpg", "broken": False}]
    cfg = load_config()
    # 1 EUR = 24.7 CZK. Item price 100 EUR -> 2470 CZK.
    with get_db_session() as session:
        session.merge(
            FxRate(
                rate_date=date.today(),
                currency="CZK",
                rate_to_base=Decimal("24.7"),
                source="ECB",
                fetched_at=datetime.now(UTC),
            )
        )
        code = _make_item(session)
        item = session.get(Item, code)
        item.target_price = Decimal("100")
        item.target_currency = "EUR"
        session.flush()
        frame = localize_frame(session, code, "bazos_cz", photos, cfg)
        assert frame["currency"] == "CZK"
        assert frame["price"] == "2470"
        # The frame stores the FX rate date.
        assert frame["price_rate_date"] == date.today().isoformat()
        _cleanup(session, code)


def test_frame_generate_and_save_routes(monkeypatch):
    """POST /frame/generate localizes + saves; POST /frame/save approves."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services import llm_filter
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(
        llm_filter,
        "call_llm",
        lambda endpoint, api_key, model, prompt: (
            '{"title": "iPhone 13", "description": "Rychlé", "category": "Telefony"}'
            if "Сформируй объявление" in prompt
            else '{"title": "Айфон", "description": "Быстрая"}'
        ),
    )

    with get_db_session() as session:
        code = create_draft_from_selection(session, [{"title": "iPhone 13", "price": "12000"}], "", None)

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        # Generate the frame (localizes + saves as localized).
        resp = c.post(
            "/ui/lot-wizard/finalize/frame/generate",
            data={"item_code": code, "mp_key": "bazos_cz"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        # Save/approve the frame.
        resp = c.post(
            "/ui/lot-wizard/finalize/frame/save",
            data={
                "item_code": code,
                "mp_key": "bazos_cz",
                "title": "iPhone 13",
                "description": "Rychlé",
                "category": "Telefony",
                "price": "12000",
                "currency": "CZK",
                "main_photo": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    with get_db_session() as session:
        item = session.get(Item, code)
        frame = item.attrs["wizard_frames"]["bazos_cz"]
        assert frame["status"] == "approved"
        assert frame["category"] == "Telefony"
        _cleanup(session, code)


def test_frame_regenerate_removes_approval(monkeypatch):
    """POST /frame/generate on an approved frame re-localizes and drops approval."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services import llm_filter
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(
        llm_filter,
        "call_llm",
        lambda endpoint, api_key, model, prompt: (
            '{"title": "iPhone 13", "description": "Rychlé", "category": "Telefony"}'
            if "Сформируй объявление" in prompt
            else '{"title": "Айфон", "description": "Быстрая"}'
        ),
    )

    with get_db_session() as session:
        code = create_draft_from_selection(session, [{"title": "iPhone 13", "price": "12000"}], "", None)

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        # Generate + approve the frame.
        c.post(
            "/ui/lot-wizard/finalize/frame/generate",
            data={"item_code": code, "mp_key": "bazos_cz"},
            follow_redirects=False,
        )
        c.post(
            "/ui/lot-wizard/finalize/frame/save",
            data={
                "item_code": code,
                "mp_key": "bazos_cz",
                "title": "iPhone 13",
                "description": "Rychlé",
                "category": "Telefony",
                "price": "12000",
                "currency": "CZK",
                "main_photo": "",
            },
            follow_redirects=False,
        )
        # Verify approved.
        with get_db_session() as session:
            frame = session.get(Item, code).attrs["wizard_frames"]["bazos_cz"]
            assert frame["status"] == "approved"
        # Re-generate: should re-localize and drop approval.
        resp = c.post(
            "/ui/lot-wizard/finalize/frame/generate",
            data={"item_code": code, "mp_key": "bazos_cz"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    with get_db_session() as session:
        item = session.get(Item, code)
        frame = item.attrs["wizard_frames"]["bazos_cz"]
        assert frame["status"] == "localized"
        assert frame["title"] == "iPhone 13"
        _cleanup(session, code)


def get_llm(monkeypatch) -> dict:
    """A configured LLM stub for direct service calls."""
    return {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""}
