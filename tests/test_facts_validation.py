import pytest

from nextlife.text.facts import FactualInconsistencyError, validate_listing_facts


def test_unmentioned_accessory_cable_fails_when_completeness_empty():
    """DoD: Text mentioning 'kabel v balení' when completeness is empty strictly fails."""
    hallucinated_text = (
        "Prodám iPhone 13. Stav nového kusu. Součástí balení je nabíjecí kabel a adaptér."
    )
    # Item completeness is empty
    item_data = {
        "completeness": [],
        "box_present": False,
        "defects": [],
        "manufacturer_warranty_until": None,
    }

    with pytest.raises(FactualInconsistencyError) as exc_info:
        validate_listing_facts(hallucinated_text, item_data)

    assert "Factual hallucination" in str(exc_info.value)
    assert "kabel" in str(exc_info.value)


def test_verified_accessory_passes():
    """Verify text mentioning accessories present in completeness passes cleanly."""
    valid_text = "Prodám iPhone 13. Součástí balení je originální kabel Lightning."
    item_data = {
        "completeness": ["Originální kabel Lightning"],
        "box_present": False,
        "defects": [],
        "manufacturer_warranty_until": None,
    }
    # Must not raise
    validate_listing_facts(valid_text, item_data)


def test_warranty_claim_without_warranty_date_fails():
    """Verify claim of warranty without documented date fails."""
    text_with_warranty = "Prodám notebook Lenovo, zařízení je v záruce."
    item_data = {
        "completeness": [],
        "manufacturer_warranty_until": None,  # No warranty date!
    }
    with pytest.raises(FactualInconsistencyError) as exc:
        validate_listing_facts(text_with_warranty, item_data)
    assert "manufacturer_warranty_until is null" in str(exc.value)


def test_claiming_no_defects_when_defects_exist_fails():
    """Verify claiming 'bez vad' when defects exist in DB strictly fails."""
    deceptive_text = "Prodám monitor Dell. Plně funkční, bez vad a škrábanců."
    item_data = {
        "completeness": [],
        "defects": [{"type": "scratch", "severity": "cosmetic"}],
    }
    with pytest.raises(FactualInconsistencyError) as exc:
        validate_listing_facts(deceptive_text, item_data)
    assert "declared defect(s)" in str(exc.value)
