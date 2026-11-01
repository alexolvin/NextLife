"""Wizard step 2: text generation contract (offline).

No external model call, no network. The LLM is mocked via llm_filter.
"""

from nextlife.core.config import load_config
from nextlife.services import llm_filter
from nextlife.services.wizard_textgen import PLACEHOLDER_TEXT, generate_description


def test_generate_description_no_llm_placeholder(monkeypatch):
    """Without a configured LLM, returns an honest placeholder (no raw dump)."""
    cfg = load_config()
    monkeypatch.setattr(
        llm_filter, "get_llm_config", lambda: {"endpoint": "", "api_key": "", "model": ""}
    )
    result = generate_description("A1", [{"title": "iPhone 13"}], "fast delivery", cfg)
    assert result["item_code"] == "A1"
    assert result["mode"] == "placeholder"
    assert result["endpoint_configured"] is False
    assert result["text"] == PLACEHOLDER_TEXT
    assert "Reference listings" not in result["text"]


def test_generate_description_llm_live(monkeypatch):
    """With a configured + mocked LLM, mode is 'live' and text comes from the LLM."""
    cfg = load_config()
    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(
        llm_filter, "call_llm", lambda ep, key, model, prompt: "Продаю быструю память DDR4."
    )
    result = generate_description("A1", [{"title": "DDR4 16GB"}], "", cfg)
    assert result["mode"] == "live"
    assert result["endpoint_configured"] is True
    assert result["text"] == "Продаю быструю память DDR4."


def test_generate_description_llm_live_json_title(monkeypatch):
    """With a configured + mocked LLM returning JSON, title and description are parsed."""
    cfg = load_config()
    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(
        llm_filter,
        "call_llm",
        lambda ep, key, model, prompt: '{"title": "RTX 3090 24GB", "description": "Карточка в отличном состоянии."}',
    )
    result = generate_description("A1", [{"title": "RTX 3090"}], "", cfg)
    assert result["mode"] == "live"
    assert result["endpoint_configured"] is True
    assert result["title"] == "RTX 3090 24GB"
    assert result["text"] == "Карточка в отличном состоянии."


def test_generate_description_llm_plain_text_fallback(monkeypatch):
    """When the LLM returns plain text (no JSON), the whole content is the description."""
    cfg = load_config()
    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(
        llm_filter, "call_llm", lambda ep, key, model, prompt: "Просто текст описания."
    )
    result = generate_description("A1", [{"title": "DDR4 16GB"}], "", cfg)
    assert result["mode"] == "live"
    assert result["title"] == ""
    assert result["text"] == "Просто текст описания."


def test_generate_description_llm_failure_placeholder(monkeypatch):
    """When the LLM call fails, fall back to the honest placeholder."""
    cfg = load_config()
    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm_filter, "call_llm", boom)
    result = generate_description("A1", [{"title": "DDR4 16GB"}], "", cfg)
    assert result["mode"] == "placeholder"
    assert result["endpoint_configured"] is True
    assert result["text"] == PLACEHOLDER_TEXT


def _cleanup(session, item_code: str) -> None:
    from nextlife.storage.models import Item, ItemPhoto, Lot

    session.query(ItemPhoto).filter(ItemPhoto.item_code == item_code).delete()
    session.query(Lot).filter(Lot.item_id == item_code).delete()
    session.query(Item).filter(Item.code == item_code).delete()
    session.commit()


def test_text_page_shows_item_fields(monkeypatch):
    """GET /ui/lot-wizard/text renders the stock form with the item's fields
    (ТЗ «Склад» §4.1 шаг 2) — not the persisted generated text."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")

    with get_db_session() as session:
        code = create_draft_from_selection(session, [{"title": "RTX 3090"}], "", None)
        item = session.get(Item, code)
        item.description = "Карточка в отличном состоянии."
        item.description_lang = "cs"
        session.commit()

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.get(f"/ui/lot-wizard/text?item_code={code}")
        assert resp.status_code == 200
        # The stock form is rendered with the item's description.
        assert "stock-form" in resp.text
        assert "Карточка в отличном состоянии." in resp.text
        # The generate button is present.
        assert "Сгенерировать описание" in resp.text

    with get_db_session() as session:
        _cleanup(session, code)


def test_text_page_redirects_without_item_code(monkeypatch):
    """GET /ui/lot-wizard/text without item_code redirects to step 1 (ТЗ §4.1)."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.get("/ui/lot-wizard/text", follow_redirects=False)
        assert resp.status_code == 303
        assert "/ui/lot-wizard/item" in resp.headers["location"]


def test_generate_writes_item_description_in_lang(monkeypatch):
    """POST /ui/lot-wizard/text generates and writes item.title + item.description
    in item.description_lang (ТЗ «Склад» §4.2.2)."""
    from starlette.testclient import TestClient

    from nextlife.api.main import create_app
    from nextlife.services import llm_filter
    from nextlife.services.wizard_draft import create_draft_from_selection
    from nextlife.storage.db import get_db_session
    from nextlife.storage.models import Item

    monkeypatch.setenv("NEXTLIFE_WEB_TOKEN", "test_token_secret")
    captured: dict = {}

    def fake_call_llm(endpoint, api_key, model, prompt):
        captured["prompt"] = prompt
        return '{"title": "RTX 3090 24GB", "description": "Karta ve skvelém stavu."}'

    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(llm_filter, "call_llm", fake_call_llm)

    with get_db_session() as session:
        code = create_draft_from_selection(session, [{"title": "RTX 3090"}], "", None)
        item = session.get(Item, code)
        item.description_lang = "cs"
        session.commit()

    app = create_app()
    with TestClient(app) as c:
        c.cookies.set("nextlife_token", "test_token_secret")
        resp = c.post("/ui/lot-wizard/text", data={"item_code": code}, follow_redirects=False)
        assert resp.status_code == 303

    with get_db_session() as session:
        item = session.get(Item, code)
        assert item.title == "RTX 3090 24GB"
        assert item.description == "Karta ve skvelém stavu."
        # The generation language is item.description_lang (cs -> «на чешском языке»).
        assert "чешском" in captured["prompt"]
        _cleanup(session, code)
