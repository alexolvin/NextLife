"""Tests for search query parser and +/- tag post-filtering."""

from decimal import Decimal

import pytest

from nextlife.connectors.base import RawListing
from nextlife.services.search_query_parser import (
    matches_query_tags,
    parse_search_query,
    term_in_text,
)
from nextlife.services.wizard_search import filter_by_query


def test_parse_search_query_basic():
    parsed = parse_search_query("ThinkPad T480")
    assert parsed.marketplace_query == "ThinkPad T480"
    assert parsed.required_terms == ()
    assert parsed.excluded_terms == ()
    assert not parsed.has_tags


def test_parse_search_query_with_required_and_excluded():
    parsed = parse_search_query("ThinkPad T480 +16gb -broken")
    assert "ThinkPad" in parsed.marketplace_query
    assert "T480" in parsed.marketplace_query
    assert "16gb" in parsed.marketplace_query
    assert "broken" not in parsed.marketplace_query
    assert parsed.required_terms == ("16gb",)
    assert parsed.excluded_terms == ("broken",)
    assert parsed.has_tags


def test_parse_search_query_with_quoted_phrases():
    parsed = parse_search_query('RTX 4070 +"dual fan" -"na dily" -defekt')
    assert "RTX 4070" in parsed.marketplace_query
    assert '"dual fan"' in parsed.marketplace_query
    assert "na dily" not in parsed.marketplace_query
    assert parsed.required_terms == ("dual fan",)
    assert parsed.excluded_terms == ("na dily", "defekt")
    assert parsed.has_tags


def test_term_in_text_variations():
    # Multi-word phrase
    assert term_in_text("dual fan", "ASUS Dual Fan RTX 4070")
    assert not term_in_text("triple fan", "ASUS Dual Fan RTX 4070")

    # Case-insensitive
    assert term_in_text("na dily", "Prodám notebook NA DÍLY nebo opravu")

    # Collapsed whitespace/units
    assert term_in_text("32gb", "Corsair Vengeance 32 GB DDR4")
    assert term_in_text("32 gb", "Corsair Vengeance 32GB DDR4")
    assert term_in_text("rtx4070", "MSI GeForce RTX 4070 Ventus")


def test_matches_query_tags():
    parsed = parse_search_query('Lenovo +32gb -broken -"na dily"')

    # 1. Matches: has 32GB, no broken, no na dily
    assert matches_query_tags(
        title="Lenovo ThinkPad T480 32GB RAM",
        description="Plně funkční notebook s nabíječkou.",
        parsed=parsed,
    )

    # 2. Required tag in description
    assert matches_query_tags(
        title="Lenovo ThinkPad T480",
        description="Paměť RAM: 32 GB, SSD 512GB.",
        parsed=parsed,
    )

    # 3. Missing required tag -> False
    assert not matches_query_tags(
        title="Lenovo ThinkPad T480 16GB RAM",
        description="Plně funkční notebook.",
        parsed=parsed,
    )

    # 4. Contains excluded single word in title -> False
    assert not matches_query_tags(
        title="Lenovo ThinkPad 32GB broken screen",
        description="Plně funkční kromě displeje.",
        parsed=parsed,
    )

    # 5. Contains excluded phrase in description -> False
    assert not matches_query_tags(
        title="Lenovo ThinkPad 32GB",
        description="Vhodné na díly, nelze zapnout.",
        parsed=parsed,
    )


def test_wizard_search_filter_by_query_with_tags():
    listings = [
        RawListing(
            title="Lenovo ThinkPad T480 32GB",
            price=Decimal("6000"),
            currency="CZK",
            external_url="https://bazos.cz/1",
            condition_text=None,
            posted_at=None,
            raw={"desc": "Krásný stav"},
        ),
        RawListing(
            title="Lenovo ThinkPad T480 16GB",
            price=Decimal("4500"),
            currency="CZK",
            external_url="https://bazos.cz/2",
            condition_text=None,
            posted_at=None,
            raw={"desc": "Krásný stav"},
        ),
        RawListing(
            title="Lenovo ThinkPad T480 32GB na díly",
            price=Decimal("2000"),
            currency="CZK",
            external_url="https://bazos.cz/3",
            condition_text=None,
            posted_at=None,
            raw={"desc": "Nefunkční deska"},
        ),
    ]

    filtered = filter_by_query(listings, 'ThinkPad +32gb -"na díly"')
    assert len(filtered) == 1
    assert filtered[0].external_url == "https://bazos.cz/1"


def test_excluded_so_dimm_tag_matches_ad_description():
    """Verify that -'SO-DIMM' rejects listings with SO-DIMM in body."""
    parsed = parse_search_query('Ddr4 32 -SODIMM -"SO-DIMM" -RDIMM')
    title = "Crucial Ballistix 32 GB (2×16 GB) DDR4 3200 CL16"
    ad_desc = "Prodám sadu pamětí. typ: DDR4 SO-DIMM, stav výborný."

    # When description is present, tag check must reject it
    assert not matches_query_tags(title, ad_desc, parsed)
    # If it was ordinary DIMM (no SO-DIMM in body), it should pass
    ordinary_desc = "Prodám sadu pamětí. typ: DDR4 DIMM pro stolní PC."
    assert matches_query_tags(title, ordinary_desc, parsed)

