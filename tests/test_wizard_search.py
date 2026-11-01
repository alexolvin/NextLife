"""Wizard step 1: normalization + Aukro «Kup teď» filtering (offline).

Tests the pure normalization and the Aukro buy-now filter with fixtures —
no live search, no network, no browser.
"""

from decimal import Decimal

from nextlife.connectors.base import RawListing
from nextlife.core.config import load_config
from nextlife.services.wizard_search import (
    filter_by_query,
    filter_results_by_intent,
    listing_matches_query,
    normalize_listing,
    search_across_marketplaces,
)


def _raw(title="Test", price="1000", currency="CZK", url="https://aukro.cz/x-1", raw=None):
    return RawListing(
        title=title,
        price=Decimal(price),
        currency=currency,
        external_url=url,
        condition_text=None,
        posted_at=None,
        raw=raw or {},
    )


def test_normalize_aukro_buy_now():
    """Aukro buy-now lot (Kup teď) passes through as type=buy_now."""
    raw = _raw(raw={"auction": False, "buy_now_active": True})
    entry = normalize_listing(raw, "aukro_cz")
    assert entry is not None
    assert entry["type"] == "buy_now"
    assert entry["platform"] == "aukro_cz"
    assert entry["price"] == "1000"
    assert entry["currency"] == "CZK"


def test_normalize_aukro_auction_filtered():
    """Aukro auction is filtered out (not a Kup teď lot)."""
    raw = _raw(raw={"auction": True, "buy_now_active": False})
    assert normalize_listing(raw, "aukro_cz") is None


def test_normalize_aukro_no_buy_now_filtered():
    """Aukro lot without buy-now is filtered out."""
    raw = _raw(raw={"auction": False, "buy_now_active": False})
    assert normalize_listing(raw, "aukro_cz") is None


def test_normalize_non_aukro_passes():
    """Non-Aukro platforms pass through as type=listing."""
    raw = _raw(url="https://www.bazos.cz/inzerat/1", raw={})
    entry = normalize_listing(raw, "bazos_cz")
    assert entry is not None
    assert entry["type"] == "listing"
    assert entry["platform"] == "bazos_cz"


def test_search_across_marketplaces_filters_aukro():
    """search_across_marketplaces filters Aukro to Kup teď, keeps others."""

    def search_fn(query, mp_key):
        if mp_key == "aukro_cz":
            return [
                _raw(title="BuyNow", raw={"auction": False, "buy_now_active": True}),
                _raw(title="Auction", raw={"auction": True, "buy_now_active": False}),
            ]
        if mp_key == "bazos_cz":
            return [_raw(title="Bazos listing", url="https://www.bazos.cz/inzerat/2")]
        return []

    cfg = load_config()
    outcome = search_across_marketplaces("notebook", ["aukro_cz", "bazos_cz"], search_fn, cfg, limit=5)
    titles = [r["title"] for r in outcome["results"]]
    assert "BuyNow" in titles
    assert "Auction" not in titles
    assert "Bazos listing" in titles
    buy_now = [r for r in outcome["results"] if r["title"] == "BuyNow"][0]
    assert buy_now["type"] == "buy_now"


def test_search_across_marketplaces_reports_errors():
    """Per-marketplace failures are reported honestly, not fabricated."""

    def search_fn(query, mp_key):
        if mp_key == "bazos_cz":
            raise ConnectionError("network down")
        return []

    cfg = load_config()
    outcome = search_across_marketplaces("notebook", ["bazos_cz"], search_fn, cfg, limit=5)
    assert "bazos_cz" in outcome["errors"]
    assert outcome["results"] == []


# --- Intent-based relevance filter (stage 3, variant A) -----------------


def _entry(title: str) -> dict:
    return {"title": title, "price": "1", "currency": "CZK", "url": "https://x", "platform": "bazos_cz", "type": "listing"}


def test_filter_intent_gpu_drops_pc_titles():
    """«rtx 3090» + «только карта»: PC-составы отбрасываются, явные карты остаются."""
    results = [
        _entry("RTX 3090 24GB"),
        _entry("Herní PC s RTX 3090"),
        _entry("Gaming PC sestava RTX 3090 + i7"),
        _entry("Nvidia RTX 3090 Founders Edition"),
    ]
    kept = [r["title"] for r in filter_results_by_intent(results, "gpu")]
    assert kept == ["RTX 3090 24GB", "Nvidia RTX 3090 Founders Edition"]


def test_filter_intent_pc_keeps_only_pc_titles():
    """«готовый ПК»: остаются только заголовки с PC-токенами."""
    results = [
        _entry("RTX 3090 24GB"),
        _entry("Herní PC s RTX 3090"),
        _entry("Kompletní PC sestava"),
    ]
    kept = [r["title"] for r in filter_results_by_intent(results, "pc")]
    assert kept == ["Herní PC s RTX 3090", "Kompletní PC sestava"]


def test_filter_intent_any_keeps_all():
    """«любое» (и пустое/неизвестное) — без фильтрации."""
    results = [_entry("RTX 3090"), _entry("Herní PC")]
    assert filter_results_by_intent(results, "any") == results
    assert filter_results_by_intent(results, "") == results
    assert filter_results_by_intent(results, "unknown") == results


def test_filter_intent_word_boundaries():
    """Токены совпадают только по границам слов: «PC» внутри слова не считается."""
    results = [_entry("RPC 3090 adapter"), _entry("PC RTX 3090")]
    kept = [r["title"] for r in filter_results_by_intent(results, "gpu")]
    assert kept == ["RPC 3090 adapter"]


def test_filter_intent_gpu_drops_skrin():
    """«skříň» (корпус) — PC-токен: «только карта» скрывает корпуса/составы."""
    results = [
        _entry("RTX 3090 24GB"),
        _entry("Skříň PC gaming + RTX 3090"),
        _entry("SKRIN s PSU a ventilátory"),
    ]
    kept = [r["title"] for r in filter_results_by_intent(results, "gpu")]
    assert kept == ["RTX 3090 24GB"]


# --- Query-string relevance filter (owner 2026-10-19) -------------------


def test_listing_matches_query_substring():
    """The query string must occur in the title or text (case/diacritic-insensitive)."""
    assert listing_matches_query("Herní PC s RTX 3090", "rtx 3090")
    assert listing_matches_query("Gigabyte GeForce RTX 3090 Ti", "RTX 3090")
    assert not listing_matches_query("Gigabyte GeForce RTX 3080", "rtx 3090")
    # Match in extra text (card desc), not only the title.
    assert listing_matches_query("Herni PC", "rtx 3090", "Značka: Nvidia, RTX 3090")
    # Diacritics: «Herní» ~ «herni».
    assert listing_matches_query("Herní PC s RTX 3090", "herni pc")
    # Empty query matches everything.
    assert listing_matches_query("anything", "")


def test_listing_matches_query_word_boundary_not_substring():
    """Word-boundary, not substring: «tricko» must not match «elektrickou».

    Owner 2026-10-20: Bazos showed a gas stove («Plynový sporák s elektrickou
    troubou») for a t-shirt search because «tricko» is a substring of
    «elektrickou».
    """
    assert not listing_matches_query("Plynový sporák s elektrickou troubou SPE 40", "tričko")
    assert listing_matches_query("Pánské tričko Canterbury, XS", "tričko")


def test_filter_by_query_drops_irrelevant():
    """Listings without the query string in title/text are dropped (pure)."""
    listings = [
        _raw(title="RTX 3090 24GB"),
        _raw(title="RTX 3080 10GB"),
        _raw(title="Herni PC", raw={"desc": "RTX 3090, i7"}),
    ]
    kept = filter_by_query(listings, "rtx 3090")
    assert [r.title for r in kept] == ["RTX 3090 24GB", "Herni PC"]


def test_filter_by_query_drops_buying_requests():
    """Buying requests (koupím/kúpim/vyměním) are dropped — they are not sales."""
    listings = [
        _raw(title="RTX 3090 24GB"),
        _raw(title="Koupím rtx 3090"),
        _raw(title="Kúpim RTX 3090 Suprim X"),
        _raw(title="vyměním/koupím RTX 3090 24GB za jinou GPU"),
    ]
    kept = filter_by_query(listings, "rtx 3090")
    assert [r.title for r in kept] == ["RTX 3090 24GB"]


def test_filter_options_no_llm_returns_none(monkeypatch):
    """Without a configured LLM, get_filter_options returns None (filter hidden)."""
    from nextlife.services import llm_filter

    monkeypatch.setattr(
        llm_filter, "get_llm_config", lambda: {"endpoint": "", "api_key": "", "model": ""}
    )
    results = [
        {"platform": "bazos_cz", "title": "DDR4 16GB"},
        {"platform": "sbazar_cz", "title": "DDR4 8GB"},
    ]
    assert llm_filter.get_filter_options("DDR4", results) is None


def test_filter_options_empty_results_returns_none(monkeypatch):
    """No results -> nothing to classify -> None (filter hidden)."""
    from nextlife.services import llm_filter

    monkeypatch.setattr(
        llm_filter, "get_llm_config", lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""}
    )
    assert llm_filter.get_filter_options("DDR4", []) is None


def test_filter_options_llm_classifies(monkeypatch):
    """With a mocked LLM, returns product-type categories + per-result assignments."""
    import json

    from nextlife.services import llm_filter

    monkeypatch.setattr(
        llm_filter, "get_llm_config", lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""}
    )
    monkeypatch.setattr(
        llm_filter,
        "call_llm",
        lambda endpoint, api_key, model, prompt: json.dumps({
            "categories": ["Оперативная память", "Готовый ПК"],
            "assignments": ["Оперативная память", "Оперативная память", "Готовый ПК"],
        }),
    )
    results = [
        {"platform": "bazos_cz", "title": "DDR4 16GB"},
        {"platform": "sbazar_cz", "title": "DDR4 8GB"},
        {"platform": "bazos_cz", "title": "PC sestava"},
    ]
    data = llm_filter.get_filter_options("DDR4", results)
    assert data is not None
    assert data["options"][0] == "Показать все"
    assert "Оперативная память" in data["options"]
    assert "Готовый ПК" in data["options"]
    assert data["assignments"] == ["Оперативная память", "Оперативная память", "Готовый ПК"]


def test_filter_options_llm_failure_returns_none(monkeypatch):
    """When the LLM call fails, get_filter_options returns None (filter hidden)."""
    from nextlife.services import llm_filter

    monkeypatch.setattr(
        llm_filter, "get_llm_config", lambda: {"endpoint": "http://127.0.0.1:1/v1", "api_key": "", "model": ""}
    )

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm_filter, "call_llm", boom)
    results = [{"platform": "bazos_cz", "title": "DDR4 16GB"}]
    assert llm_filter.get_filter_options("DDR4", results) is None
