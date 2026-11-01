
import pytest

from nextlife.connectors.prepublish import (
    PrepublishValidationError,
    validate_lot_for_publication,
)
from nextlife.core.config import load_config


@pytest.fixture
def bazos_config():
    config = load_config()
    return config.marketplaces["bazos_cz"]


@pytest.fixture
def valid_lot():
    return {
        "title": "NVIDIA GeForce RTX 3090 24GB",
        "description": (
            "Prodám grafickou kartu ve výborném stavu.\n\n"
            "UPOZORNĚNÍ:\n"
            "- Jedná se o soukromý prodej.\n"
            "- V souladu s § 1916 OZ vyloučeno.\n"
            "- Bez nároku na vrácení do 14 dnů.\n"
        ),
        "price": 12000,
        "photos": ["photo_1", "photo_2", "photo_3"],
        "defects": [
            {"zone": "ventilátor", "text": "rezonance", "photo_id": "photo_2"}
        ],
    }


def test_validate_lot_for_publication_success(valid_lot, bazos_config):
    report = validate_lot_for_publication(valid_lot, bazos_config)
    assert report.is_valid is True
    assert report.marketplace_key == "bazos_cz"
    assert len(report.errors) == 0


def test_validate_lot_rejects_internal_field_leak(valid_lot, bazos_config):
    valid_lot["price_floor"] = 10000
    with pytest.raises(PrepublishValidationError, match="Internal field leak detected"):
        validate_lot_for_publication(valid_lot, bazos_config)


def test_validate_lot_rejects_excessive_photos(valid_lot, bazos_config):
    valid_lot["photos"] = [f"photo_{i}" for i in range(15)]  # limit is 10
    with pytest.raises(PrepublishValidationError, match="exceeds 'bazos_cz' limit of 10"):
        validate_lot_for_publication(valid_lot, bazos_config)


def test_validate_lot_rejects_excessive_title(valid_lot, bazos_config):
    valid_lot["title"] = "X" * 65  # limit is 60
    with pytest.raises(PrepublishValidationError, match="Title length"):
        validate_lot_for_publication(valid_lot, bazos_config)


def test_validate_lot_rejects_unlinked_defect_photo(valid_lot, bazos_config):
    # Defect links to photo_99, which is missing from photoset
    valid_lot["defects"] = [
        {"zone": "konektor", "text": "odlomený pin", "photo_id": "photo_99"}
    ]
    with pytest.raises(PrepublishValidationError, match="missing mandatory linked photo"):
        validate_lot_for_publication(valid_lot, bazos_config)


def test_validate_lot_rejects_price_below_floor(valid_lot, bazos_config):
    valid_lot["price"] = 8000
    valid_lot["price_floor_check"] = 10000
    with pytest.raises(PrepublishValidationError, match="cannot be lower than price_floor"):
        validate_lot_for_publication(valid_lot, bazos_config)


def test_validate_lot_rejects_missing_legal_disclaimer(valid_lot, bazos_config):
    valid_lot["description"] = "Jen prostý popis bez povinných právních doložek."
    with pytest.raises(PrepublishValidationError, match="Legal disclaimer error"):
        validate_lot_for_publication(valid_lot, bazos_config)
