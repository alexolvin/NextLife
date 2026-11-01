"""Bazoš category path: section subdomain + rubric dropdown. No browser."""

import pytest


def test_parse_category_splits_section_and_rubric():
    from nextlife.services.bazos_publish import parse_category

    assert parse_category("pc/Notebooky") == ("pc", "Notebooky")
    assert parse_category(" Foto/Objektivy ") == ("foto", "Objektivy")
    assert parse_category("notebook") == ("", "notebook")
    assert parse_category("") == ("", "")


def test_match_rubric_ignores_diacritics_and_rejects_ties():
    from nextlife.services.bazos_publish import match_category_option

    options = [
        ("", "Vyberte"),
        ("12", "Notebooky"),
        ("13", "Grafické karty"),
        ("14", "Ostatní - bílá"),
        ("15", "Ostatní audio video"),
        ("16", "Apple"),
    ]
    chosen, competing = match_category_option(options, "notebooky")
    assert chosen == ("12", "Notebooky")
    assert competing == []

    chosen, competing = match_category_option(options, "graficke karty")
    assert chosen == ("13", "Grafické karty")

    chosen, competing = match_category_option(options, "iphone")
    assert chosen is None
    assert competing == []

    chosen, competing = match_category_option(options, "ostatni")
    assert chosen is None
    assert "Ostatní - bílá" in competing
    assert "Ostatní audio video" in competing


def test_publish_urls_substitute_known_section_only():
    from nextlife.services.bazos_publish import _publish_urls

    login = _publish_urls()
    assert login["login"].endswith("/moje-inzeraty.php")
    assert login["form"] == ""
    assert "{top}" not in login["login"]

    form = _publish_urls("foto")["form"]
    assert form == "https://foto.bazos.cz/pridat-inzerat.php"
    assert "{top}" not in form

    with pytest.raises(ValueError):
        _publish_urls("pc.evil")
    with pytest.raises(ValueError):
        _publish_urls("notasection")


def test_unique_rubric_resolves_without_a_section_prefix():
    """Lot 130 stored «Grafické karty» before paths existed. It is only under PC."""
    from nextlife.services.bazos_publish import category_path_error, resolve_category_path

    assert resolve_category_path("Grafické karty") == "pc/Grafické karty"
    assert category_path_error("Grafické karty") is None
    assert resolve_category_path("Objektivy") == "foto/Objektivy"


def test_publish_refuses_ambiguous_category_before_browser():
    """A rubric that sits in several sections must not open Chrome."""
    from nextlife.services.bazos_publish import bazos_publish_listing

    missing = bazos_publish_listing(
        title="Objektiv", description="test", price="100", category="Ostatní"
    )
    assert missing["ok"] is False
    assert "Устранение" in missing["errors"][0]
    assert "нескольких разделах" in missing["errors"][0]

    unknown = bazos_publish_listing(
        title="Objektiv", description="test", price="100", category="evil/Objektivy"
    )
    assert unknown["ok"] is False
    assert "Устранение" in unknown["errors"][0]
    assert "foto" in unknown["errors"][0]


def test_config_tree_lists_live_rubrics():
    from nextlife.core.config import load_config

    tree = {node["top"]: node for node in load_config().marketplaces["bazos_cz"].category_tree}
    assert "Notebooky" in tree["pc"]["subs"]
    assert "Objektivy" in tree["foto"]["subs"]
    assert "Apple" in tree["mobil"]["subs"]
    assert "iphone" not in [item.lower() for item in tree["mobil"]["subs"]]


def test_localize_prompt_lists_rubric_not_a_model_name(monkeypatch):
    from nextlife.core.config import load_config
    from nextlife.services import llm_filter
    from nextlife.services.wizard_localize import localize_listing_text

    captured: dict[str, str] = {}

    def fake_call_llm(endpoint, api_key, model, prompt):
        captured["prompt"] = prompt
        return '{"title": "iPhone 13", "description": "Text", "category": "mobil/Apple"}'

    monkeypatch.setattr(
        llm_filter,
        "get_llm_config",
        lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    monkeypatch.setattr(llm_filter, "call_llm", fake_call_llm)

    mp = load_config().marketplaces["bazos_cz"]
    out = localize_listing_text(
        "Айфон", "текст", mp, "electronics",
        {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""},
    )
    assert out["category"] == "mobil/Apple"
    assert "Notebooky" in captured["prompt"]
    assert "mobil/Apple" in captured["prompt"]
    assert "mobil/iphone" not in captured["prompt"]
