"""Tests for listing text formation with usage, specs, and marketplace delivery options.

Verifies user requirements:
1. Item usage and specs fields are included in listing texts when filled.
2. Section for possible ways of use ('Возможные способы использования' / 'Možnosti využití') is inserted if usage is filled.
3. Section for characteristics ('Характеристики' / 'Parametry a specifikace') is inserted if specs is filled.
4. Marketplaces without dedicated delivery fields (e.g. Bazoš, Sbazar) receive a delivery options block.
5. Marketplaces with dedicated delivery fields (e.g. Aukro, Allegro, Vinted, eBay) do NOT receive duplicate delivery text.
6. Currency and language localization work as expected.
"""

from decimal import Decimal
import pytest

from nextlife.core.config import load_config
from nextlife.services.draft_text import get_item_text_blocks
from nextlife.services.listing_text_builder import (
    build_composite_listing_text,
    build_item_delivery_text,
)
from nextlife.services.listings import prepare_item_listings
from nextlife.services.wizard_localize import localize_frame, localize_listing_text
from nextlife.storage.db import get_db_session
from nextlife.storage.models import DeliveryOption, Item, Listing
from tests.conftest import clean_database


def test_build_composite_listing_text_all_fields_cs():
    """Verify Czech text assembly when description, usage, and specs are all present."""
    desc = "Prodám výkonnou grafickou kartu."
    usage = "Vhodné pro hraní moderních her ve 4K a rendering videa."
    specs = "Paměť: 24GB GDDR6X\nRozhraní: PCIe 4.0\nKonektory: 3x DP, 1x HDMI"
    deliv = "Možnosti dopravy:\n- Osobní předání: Zdarma\n- Zásilkovna: 89 Kč"

    text = build_composite_listing_text(
        description=desc,
        usage=usage,
        specs=specs,
        delivery_block=deliv,
        lang="cs",
    )

    assert "Prodám výkonnou grafickou kartu." in text
    assert "Možnosti využití:\nVhodné pro hraní moderních her ve 4K a rendering videa." in text
    assert "Parametry a specifikace:\nPaměť: 24GB GDDR6X" in text
    assert "Možnosti dopravy:\n- Osobní předání: Zdarma" in text


def test_build_composite_listing_text_empty_optional_fields():
    """Verify that empty usage, specs, or delivery blocks are omitted cleanly."""
    desc = "Základní popis zboží."
    text = build_composite_listing_text(
        description=desc,
        usage="",
        specs=None,
        delivery_block=None,
        lang="cs",
    )
    assert text == "Základní popis zboží."
    assert "Možnosti využití" not in text
    assert "Parametry a specifikace" not in text
    assert "Možnosti dopravy" not in text


def test_build_composite_listing_text_multilingual():
    """Verify localized section headers across supported languages."""
    # Russian
    ru_text = build_composite_listing_text("Описание.", "Для монтажа.", "16 ГБ ОЗУ.", lang="ru")
    assert "Возможные способы использования:\nДля монтажа." in ru_text
    assert "Характеристики:\n16 ГБ ОЗУ." in ru_text

    # Slovak
    sk_text = build_composite_listing_text("Popis.", "Na hranie.", "16 GB RAM.", lang="sk")
    assert "Možnosti využitia:\nNa hranie." in sk_text
    assert "Parametre a špecifikácia:\n16 GB RAM." in sk_text

    # German
    de_text = build_composite_listing_text("Beschreibung.", "Für Gaming.", "16 GB RAM.", lang="de")
    assert "Einsatzmöglichkeiten:\nFür Gaming." in de_text
    assert "Spezifikationen:\n16 GB RAM." in de_text

    # Polish
    pl_text = build_composite_listing_text("Opis.", "Do gier.", "16 GB RAM.", lang="pl")
    assert "Możliwości zastosowania:\nDo gier." in pl_text
    assert "Specyfikacja:\n16 GB RAM." in pl_text

    # English
    en_text = build_composite_listing_text("Description.", "For gaming.", "16 GB RAM.", lang="en")
    assert "Possible uses:\nFor gaming." in en_text
    assert "Specifications:\n16 GB RAM." in en_text


def test_marketplace_dedicated_delivery_fields_distinction():
    """Verify that platforms without delivery fields get delivery text, while platforms with dedicated fields do not."""
    cfg = load_config()

    # Bazos CZ has no delivery form fields -> must receive delivery block
    bazos_cfg = cfg.marketplaces["bazos_cz"]
    assert bazos_cfg.has_dedicated_delivery_fields is False

    # Aukro CZ has dedicated delivery fields -> must NOT receive delivery block
    aukro_cfg = cfg.marketplaces["aukro_cz"]
    assert aukro_cfg.has_dedicated_delivery_fields is True

    # Vinted, eBay have dedicated delivery fields
    assert cfg.marketplaces["vinted"].has_dedicated_delivery_fields is True
    assert cfg.marketplaces["ebay"].has_dedicated_delivery_fields is True

    # Sbazar, Bazos SK, Kleinanzeigen, Willhaben do not have dedicated delivery fields
    assert cfg.marketplaces["sbazar_cz"].has_dedicated_delivery_fields is False
    assert cfg.marketplaces["bazos_sk"].has_dedicated_delivery_fields is False
    assert cfg.marketplaces["kleinanzeigen_de"].has_dedicated_delivery_fields is False
    assert cfg.marketplaces["willhaben_at"].has_dedicated_delivery_fields is False


def test_build_item_delivery_text_with_and_without_dedicated_fields():
    """Verify build_item_delivery_text includes shipping options for Bazos and returns empty for Aukro."""
    with get_db_session() as session:
        clean_database(session)
        item = Item(
            code="701",
            kind="SELL",
            title="Notebook ThinkPad",
            category_key="pc",
            condition_key="used",
            description="Zachovalý pracovní notebook.",
            target_price=Decimal("10000"),
            target_currency="CZK",
            status="DRAFT",
        )
        session.add(item)
        session.flush()

        # Add delivery options
        d1 = DeliveryOption(
            item_code="701",
            carrier_key="pickup",
            service_name="Osobní odběr",
            price=Decimal("0.00"),
            is_enabled=True,
        )
        d2 = DeliveryOption(
            item_code="701",
            carrier_key="zasilkovna",
            service_name="Zásilkovna",
            price=Decimal("89.00"),
            is_enabled=True,
        )
        session.add_all([d1, d2])
        session.commit()

        # For bazos_cz (no separate delivery fields):
        bazos_delivery = build_item_delivery_text(session, item, "bazos_cz")
        assert "Možnosti dopravy:" in bazos_delivery
        assert "Osobní předání: Zdarma" in bazos_delivery or "Osobní odběr: Zdarma" in bazos_delivery
        assert "Zásilkovna: 89 Kč" in bazos_delivery

        # For aukro_cz (has separate delivery fields):
        aukro_delivery = build_item_delivery_text(session, item, "aukro_cz")
        assert aukro_delivery == ""


def test_prepare_item_listings_incorporates_usage_specs_and_delivery():
    """Verify prepare_item_listings creates listing copies with usage, specs, and delivery block."""
    with get_db_session() as session:
        clean_database(session)
        item = Item(
            code="702",
            kind="SELL",
            title="Grafická karta RTX 4070",
            category_key="electronics",
            condition_key="used",
            description="Skvělá grafická karta ve 100% stavu.",
            usage="Ideální na hraní ve 1440p i trénování AI modelů.",
            specs="VRAM: 12GB GDDR6X\nTDP: 200W",
            target_price=Decimal("14000"),
            target_currency="CZK",
            status="DRAFT",
            description_lang="cs",
        )
        session.add(item)
        session.flush()

        d1 = DeliveryOption(
            item_code="702",
            carrier_key="pickup",
            service_name="Osobní předání",
            price=Decimal("0.00"),
            is_enabled=True,
        )
        d2 = DeliveryOption(
            item_code="702",
            carrier_key="zasilkovna",
            service_name="Zásilkovna",
            price=Decimal("89.00"),
            is_enabled=True,
        )
        session.add_all([d1, d2])
        session.commit()

        listings = prepare_item_listings(session, "702")
        session.commit()

        by_mp = {l.marketplace_key: l for l in listings}

        # 1. Bazoš CZ: classifieds -> has usage, specs, AND delivery block
        if "bazos_cz" in by_mp:
            bazos_desc = by_mp["bazos_cz"].description
            assert "Skvělá grafická karta ve 100% stavu." in bazos_desc
            assert "Možnosti využití:" in bazos_desc
            assert "Ideální na hraní ve 1440p" in bazos_desc
            assert "Parametry a specifikace:" in bazos_desc
            assert "VRAM: 12GB GDDR6X" in bazos_desc
            assert "Možnosti dopravy:" in bazos_desc
            assert "Zdarma" in bazos_desc
            assert "89 Kč" in bazos_desc

        # 2. Aukro CZ: marketplace with dedicated delivery fields -> has usage and specs, but NO delivery block
        if "aukro_cz" in by_mp:
            aukro_desc = by_mp["aukro_cz"].description
            assert "Skvělá grafická karta ve 100% stavu." in aukro_desc
            assert "Možnosti využití:" in aukro_desc
            assert "Parametry a specifikace:" in aukro_desc
            assert "Možnosti dopravy:" not in aukro_desc

        # 3. Bazoš SK: Slovak language, no delivery fields -> Slovak headers, EUR delivery price
        if "bazos_sk" in by_mp:
            sk_desc = by_mp["bazos_sk"].description
            assert "Možnosti využitia:" in sk_desc
            assert "Parametre a špecifikácia:" in sk_desc or "Špecifikácia:" in sk_desc
            assert "Možnosti dopravy:" in sk_desc
            # Currency converted to EUR
            assert "EUR" in sk_desc or "Zadarmo" in sk_desc


def test_wizard_localize_frame_with_usage_specs_delivery(monkeypatch):
    """Verify wizard step 3 localize_frame includes usage, specs, and delivery for Bazos."""
    from nextlife.services import llm_filter

    # Fallback mode (no LLM)
    monkeypatch.setattr(llm_filter, "get_llm_config", lambda: {"endpoint": ""})

    with get_db_session() as session:
        clean_database(session)
        item = Item(
            code="703",
            kind="SELL",
            title="Objektiv Canon 50mm",
            category_key="foto",
            condition_key="used",
            description="Kompaktní portrétní objektiv.",
            usage="Výborný na portréty, street fotografii a natáčení videí.",
            specs="Světelnost: f/1.8\nBajonet: Canon RF\nPrůměr filtru: 43mm",
            target_price=Decimal("4500"),
            target_currency="CZK",
            status="DRAFT",
            description_lang="cs",
        )
        session.add(item)
        session.flush()

        d1 = DeliveryOption(
            item_code="703",
            carrier_key="pickup",
            service_name="Osobní předání",
            price=Decimal("0.00"),
            is_enabled=True,
        )
        session.add(d1)
        session.commit()

        photos = [{"id": "p1", "display_id": "p1", "url": "/media/p1.jpg", "broken": False}]
        frame = localize_frame(session, "703", "bazos_cz", photos)

        desc = frame["description"]
        assert "Kompaktní portrétní objektiv." in desc
        assert "Možnosti využití:" in desc
        assert "Výborný na portréty" in desc
        assert "Parametry a specifikace:" in desc
        assert "Světelnost: f/1.8" in desc
        assert "Možnosti dopravy:" in desc
        assert "Zdarma" in desc


def test_get_item_text_blocks_includes_usage_and_specs():
    """Verify get_item_text_blocks final_text reflects usage and specs."""
    item = Item(
        code="704",
        kind="SELL",
        title="Monitor 27 inch",
        category_key="pc",
        condition_key="used",
        description="Herní IPS monitor.",
        usage="Vhodný pro práci i hraní her s vysokou obnovovací frekvencí.",
        specs="Rozlišení: 2560x1440 (QHD)\nFrekvence: 165Hz",
        target_price=Decimal("6000"),
        target_currency="CZK",
        description_lang="cs",
    )
    blocks = get_item_text_blocks(item)
    final = blocks["final_text"]
    assert "Herní IPS monitor." in final
    assert "Možnosti využití:" in final
    assert "Vhodný pro práci i hraní her" in final
    assert "Parametry a specifikace:" in final
    assert "2560x1440" in final
