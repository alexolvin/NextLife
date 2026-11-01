"""Unit tests for the search results parser (Stage 2)."""

from pathlib import Path

from nextlife.android.results import parse_results

FIX = Path(__file__).parent / "fixtures" / "android"
SEARCH = FIX.joinpath("vinted_search_results.xml").read_text(encoding="utf-8")


def test_parse_vinted_results_all_cards() -> None:
    results = parse_results(SEARCH, "item_box_container", limit=10)
    assert len(results) == 4
    # First card
    assert results[0].title == "Apple Iphone 12 Pro 256GB 100% Kondycji Stan Idealny + Gratisy"
    assert results[0].price == "3954.14"
    assert results[0].currency == "Kč"
    # Second card
    assert results[1].title == "iPhone Air"
    assert results[1].price == "15694.59"
    assert results[1].currency == "Kč"
    # Third card
    assert results[2].title == "iPhone 4 8 GB – klasyczny iPhone / iPod z grami / ładny stan!"
    assert results[2].price == "728.69"
    assert results[2].currency == "Kč"
    # Fourth card
    assert results[3].title == "iPhone 17 Pro Max 512GB Srebrny"
    assert results[3].price == "26549.25"
    assert results[3].currency == "Kč"


def test_parse_vinted_results_limit() -> None:
    results = parse_results(SEARCH, "item_box_container", limit=2)
    assert len(results) == 2
    assert results[0].title.startswith("Apple Iphone 12 Pro")
    assert results[1].title == "iPhone Air"


def test_parse_results_no_card_selector_returns_empty() -> None:
    assert parse_results(SEARCH, "", limit=5) == []
    assert parse_results(SEARCH, "nonexistent_id", limit=5) == []


def test_parse_results_empty_xml() -> None:
    assert parse_results("", "item_box_container", limit=5) == []
    assert parse_results("not xml at all", "item_box_container", limit=5) == []


def test_parse_results_card_desc_preserved() -> None:
    results = parse_results(SEARCH, "item_box_container", limit=1)
    assert "Značka: Apple" in results[0].card_desc
    assert "Oblíbeno" in results[0].card_desc


def test_parse_price_known_currency_wins_over_title_word() -> None:
    """'RTX 3060 TUF' must not be read as price 3060 currency TUF: the known
    currency (7118.23 Kč) wins (owner report 2026-10-19)."""
    from nextlife.android.results import _parse_desc

    title, price, currency = _parse_desc("RTX 3060 TUF Gaming, Značka: ASUS, 7118.23 Kč")
    assert title == "RTX 3060 TUF Gaming"
    assert price == "7118.23"
    assert currency == "Kč"


def test_parse_price_fallback_unknown_currency() -> None:
    """Without any known currency the first number+3-letters match is kept."""
    from nextlife.android.results import _parse_desc

    title, price, currency = _parse_desc("RTX 3060 TUF")
    assert title == "RTX 3060 TUF"
    assert price == "3060"
    assert currency == "TUF"
