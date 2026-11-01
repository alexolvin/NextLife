import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError
from nextlife.text.assembler import (
    AssemblerError,
    MarketplaceLimitExceededError,
    assemble_listing_text,
)
from nextlife.text.czech import CzechCalqueDetectedError
from nextlife.text.facts import FactualInconsistencyError


@pytest.fixture
def rtx_item_data():
    return {
        "title": "NVIDIA GeForce RTX 3090 24GB",
        "category": "gpu",
        "condition_grade": "B",
        "box_present": True,
        "receipt_present": False,
        "seller_guarantee_days": 7,
        "description": "Karta používána v dobře větrané skříni.",
        "usage_declaration": "běžné hraní o víkendech",
        "mining": False,
        "defects": [
            {
                "zone": "ventilátor 2",
                "text": "při zátěži nad 75 % mírně rezonuje",
                "photo_id": "orig_photo_03",
            }
        ],
        "completeness": ["originální podpěrný držák"],
    }


@pytest.fixture
def delivery_options():
    return [
        {
            "name": "Zásilkovna na výdejní místo",
            "buyer_total": 12079,
            "currency": "Kč",
            "details": "cena zboží 12 000 Kč + doprava 79 Kč",
        },
        {
            "name": "Osobní převzetí Praha",
            "buyer_total": 12000,
            "currency": "Kč",
            "details": "zdarma",
        },
    ]


def test_assemble_listing_text_all_sections_present(rtx_item_data, delivery_options):
    listing = assemble_listing_text(
        item_data=rtx_item_data,
        delivery_options=delivery_options,
        marketplace_key="bazos_cz",
    )

    body = listing.body
    # 1. Title
    assert "NVIDIA GeForce RTX 3090 24GB" in body
    # 2. Condition & definition
    assert "Velmi dobrý stav" in body
    assert "Stupeň B" in body
    # 3. Box & receipt
    assert "Krabice / originální balení: Ano" in body
    assert "Doklad o koupi / účtenka: Ne" in body
    # 4. Guarantee
    assert "7 dní na důkladné vyzkoušení" in body
    # 5. Usage profile (GPU mining negation)
    assert "netěžilo (žádný mining)" in body
    # 6. Defects with photo linkage
    assert "ventilátor 2: při zátěži nad 75 % mírně rezonuje (viz foto #orig_photo_03)" in body
    # 7. Completeness
    assert "originální podpěrný držák" in body
    # 8. Delivery options
    assert "Zásilkovna na výdejní místo: 12079 Kč" in body
    assert "Osobní převzetí Praha: 12000 Kč" in body
    # 9. Legal disclaimer
    assert "soukromý prodej" in body
    assert "1916" in body
    assert "14 dnů" in body
    # 10. Attributes
    assert listing.char_count == len(body)
    assert listing.marketplace_key == "bazos_cz"


def test_assemble_listing_rejects_empty_title(rtx_item_data, delivery_options):
    rtx_item_data["title"] = "   "
    with pytest.raises(AssemblerError, match="Listing title cannot be empty"):
        assemble_listing_text(rtx_item_data, delivery_options)


def test_assemble_listing_rejects_internal_field_leakage(rtx_item_data, delivery_options):
    # If price_floor is inadvertently in context or description
    rtx_item_data["description"] = "price_floor je tajný limit"
    with pytest.raises(InternalFieldLeakageError):
        assemble_listing_text(rtx_item_data, delivery_options)


def test_assemble_listing_rejects_unmentioned_accessory(rtx_item_data, delivery_options):
    # Description claims HDMI kabel included, but completeness is missing it
    rtx_item_data["description"] = "V balení je HDMI kabel."
    with pytest.raises(FactualInconsistencyError):
        assemble_listing_text(rtx_item_data, delivery_options)


def test_assemble_listing_rejects_calques(rtx_item_data, delivery_options):
    rtx_item_data["description"] = "Karta je in great condition."
    with pytest.raises(CzechCalqueDetectedError):
        assemble_listing_text(rtx_item_data, delivery_options)


def test_assemble_listing_title_exceeds_marketplace_limit(rtx_item_data, delivery_options):
    rtx_item_data["title"] = "X" * 65  # bazos max_title_len is 60
    with pytest.raises(MarketplaceLimitExceededError, match="Title length 65 exceeds bazos_cz limit of 60"):
        assemble_listing_text(rtx_item_data, delivery_options, marketplace_key="bazos_cz")
