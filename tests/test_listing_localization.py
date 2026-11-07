"""Tests for listing localization and marketplace language matching."""

from decimal import Decimal
import pytest
from nextlife.core.config import load_config
from nextlife.services.localize import (
    get_marketplace_language,
    translate_ad_text,
    translate_listing_content,
)
from nextlife.services.listings import prepare_item_listings
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Listing


def test_marketplace_language_mapping():
    """Verify that every marketplace resolves to its native language code."""
    config = load_config()

    expected = {
        "bazos_sk": "sk",
        "olx_pl": "pl",
        "kleinanzeigen_de": "de",
        "willhaben_at": "de",
        "ebay": "en",
        "bazos_cz": "cs",
        "sbazar_cz": "cs",
        "bazar_cz": "cs",
        "aukro_cz": "cs",
        "annonce_cz": "cs",
        "hyperinzerce_cz": "cs",
        "mimibazar_cz": "cs",
        "allegro_cz": "cs",
        "trhknih_cz": "cs",
        "vinted": "cs",
        "facebook_marketplace": "cs",
    }

    for mp_key, expected_lang in expected.items():
        mp_cfg = config.marketplaces.get(mp_key)
        assert mp_cfg is not None, f"Marketplace {mp_key} not in config"
        actual_lang = get_marketplace_language(mp_cfg, mp_key)
        assert actual_lang == expected_lang, f"Expected {expected_lang} for {mp_key}, got {actual_lang}"


def test_translation_slovak_rules_fallback():
    """Verify Slovak translation converts key ad phrasing even without live AI."""
    czech_text = "Prodám grafickou kartu ve stavu jako nová. Pošlu kamkoliv po ČR."
    translated = translate_ad_text(czech_text, target_lang="sk", source_lang="cs")

    assert "Predám" in translated, f"Expected 'Predám' in Slovak translation, got: {translated}"
    assert "v stave ako nová" in translated, f"Expected 'v stave ako nová', got: {translated}"
    assert "Pošlem" in translated, f"Expected 'Pošlem', got: {translated}"


def test_translation_polish_rules_fallback():
    """Verify Polish translation converts key ad phrasing."""
    czech_text = "Prodám grafickou kartu ve stavu jako nová."
    translated = translate_ad_text(czech_text, target_lang="pl", source_lang="cs")

    assert "Sprzedam" in translated, f"Expected 'Sprzedam' in Polish translation, got: {translated}"
    assert "jak nowa" in translated, f"Expected 'jak nowa', got: {translated}"


def test_translation_german_rules_fallback():
    """Verify German translation converts key ad phrasing."""
    czech_text = "Prodám grafickou kartu ve stavu jako nová."
    translated = translate_ad_text(czech_text, target_lang="de", source_lang="cs")

    assert "verkaufe" in translated.lower(), f"Expected 'verkaufe' in German translation, got: {translated}"
    assert "wie neu" in translated or "neuwertig" in translated, f"Expected 'wie neu', got: {translated}"


def test_prepare_item_listings_localizes_language():
    """Verify prepare_item_listings generates localized draft listings with correct lang."""
    from tests.conftest import clean_database
    with get_db_session() as session:
        clean_database(session)
        item = Item(
            code="888",
            kind="SELL",
            title="Testovací grafická karta",
            category_key="electronics",
            condition_key="used",
            description="Prodám grafickou kartu ve stavu jako nová.",
            target_price=Decimal("1500"),
            target_currency="CZK",
            status="DRAFT",
            description_lang="cs",
        )
        session.add(item)
        session.commit()

        listings = prepare_item_listings(session, "888")
        session.commit()

        by_mp = {l.marketplace_key: l for l in listings}

        if "bazos_sk" in by_mp:
            sk_listing = by_mp["bazos_sk"]
            assert sk_listing.lang == "sk"
            assert "Predám" in sk_listing.description or "predám" in sk_listing.description.lower()

        if "bazos_cz" in by_mp:
            cz_listing = by_mp["bazos_cz"]
            assert cz_listing.lang == "cs"
            assert "Prodám" in cz_listing.description
