from decimal import Decimal

from nextlife.shipping.packaging import (
    calculate_packaging,
    get_packaging_checklist,
    load_packaging_types,
)


def test_load_packaging_types():
    types = load_packaging_types()
    assert len(types) >= 4
    assert "box_small" in types
    assert "envelope" in types
    assert types["box_small"].cost_czk == Decimal("30.00")


def test_calculate_packaging_dimensions_and_cost():
    res = calculate_packaging(
        raw_weight_g=1000,
        raw_l_mm=200,
        raw_w_mm=100,
        raw_h_mm=50,
        packaging_type="box_medium",
    )
    # Box medium adds: 300g, 50mm, 50mm, 50mm, cost 50.00
    assert res["packed_weight_g"] == 1300
    assert res["packed_l_mm"] == 250
    assert res["packed_w_mm"] == 150
    assert res["packed_h_mm"] == 100
    assert res["packaging_cost_czk"] == Decimal("50.00")


def test_packaging_checklist_fragile_hardware():
    """DoD: Verify checklist generates category-specific and fragile-specific items."""
    checklist = get_packaging_checklist(category_key="gpu_hardware", fragile=True)
    joined = " ".join(checklist)

    # Hardware specific
    assert "антистатический пакет" in joined
    assert "заглушки" in joined

    # Fragile specific
    assert "KŘEHKÉ / FRAGILE" in joined
    assert "воздушно-пузырчатой" in joined

    # General dispatch requirements
    assert "фото содержимого" in joined


def test_packaging_checklist_non_fragile_general():
    """Verify non-fragile item gets standard checklist without fragile warnings."""
    checklist = get_packaging_checklist(category_key="clothing", fragile=False)
    joined = " ".join(checklist)
    assert "KŘEHKÉ" not in joined
    assert "антистатический" not in joined
    assert "фото содержимого" in joined
