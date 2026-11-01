import pytest

from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item
from nextlife.storage.models.defects import (
    DefectValidationError,
    ItemDefect,
    validate_defect_data,
)


def test_defect_without_photo_fails_validation():
    """DoD: A declared defect without a linked photo strictly fails validation."""
    defect_without_photo = {
        "defect_type": "scratch",
        "severity": "cosmetic",
        "description_owner": "Царапина на стекле",
        "description_cs": "Škrábanec na skle",
        "photo_id": "",  # Missing photo!
        "crop_box": {"x": 100, "y": 100, "width": 50, "height": 50},
    }

    with pytest.raises(DefectValidationError) as exc_info:
        validate_defect_data(defect_without_photo)

    assert "lacks mandatory linked photo" in str(exc_info.value)


def test_defect_with_photo_and_highlight_coordinates_passes():
    """DoD: Defect with valid photo and highlight/arrow coordinates passes and stores in DB."""
    defect_valid = {
        "defect_type": "scratch",
        "severity": "cosmetic",
        "description_owner": "Небольшая потёртость на левом углу",
        "description_cs": "Drobné oděrky na levém rohu",
        "photo_id": "photo_uuid_12345",
        "crop_box": {"x": 350, "y": 210, "width": 80, "height": 60},
    }

    schema = validate_defect_data(defect_valid)
    assert schema.photo_id == "photo_uuid_12345"
    assert schema.crop_box.x == 350
    assert schema.crop_box.width == 80

    with get_db_session() as session:
        try:
            item = Item(
                code="996",
                kind="SELL",
                title="Item with Scratch",
                category_key="general",
                attrs={},
                condition_key="used",
                defects={},
                status="DRAFT",
            )
            session.add(item)
            session.commit()

            defect_db = ItemDefect(
                item_code="996",
                defect_type=schema.defect_type,
                severity=schema.severity,
                description_owner=schema.description_owner,
                description_cs=schema.description_cs,
                photo_id=schema.photo_id,
                crop_box=schema.crop_box.model_dump(),
            )
            session.add(defect_db)
            session.commit()

            loaded = session.query(ItemDefect).filter_by(item_code="996").first()
            assert loaded is not None
            assert loaded.photo_id == "photo_uuid_12345"
            assert loaded.crop_box["x"] == 350
        finally:
            session.query(ItemDefect).filter_by(item_code="996").delete()
            session.query(Item).filter_by(code="996").delete()
            session.commit()
