from nextlife.shipping.dimensions import (
    apply_packaging,
    calculate_sum_of_sides,
    calculate_volumetric_weight_g,
    check_dimension_limits,
    get_longest_side,
)


def test_calculate_sum_of_sides_and_longest_side():
    assert calculate_sum_of_sides(300, 200, 100) == 600
    assert get_longest_side(300, 200, 100) == 300
    assert get_longest_side(50, 450, 120) == 450


def test_volumetric_weight_calculation():
    # 50cm x 40cm x 30cm = 60,000 / 5000 = 12 kg = 12000 g
    vol_g = calculate_volumetric_weight_g(500, 400, 300, divisor=5000)
    assert vol_g == 12000


def test_apply_packaging_increases_dimensions_and_weight(tmp_path):
    packed_w, packed_l, packed_width, packed_h = apply_packaging(
        raw_weight_g=1000,
        raw_l_mm=250,
        raw_w_mm=150,
        raw_h_mm=50,
        packaging_type="box_small",
    )
    assert packed_w > 1000
    assert packed_l > 250
    assert packed_width > 150
    assert packed_h > 50


def test_boundary_values_exact_limit_is_accepted():
    """DoD: item exactly on the boundary of service limit MUST be considered acceptable (<=)."""
    # Exact weight boundary
    fits, reason = check_dimension_limits(
        packed_weight_g=5000,
        l_mm=200,
        w_mm=200,
        h_mm=200,
        weight_max_g=5000,
        dim_rule={"max_sum_of_sides_mm": 1200, "max_single_side_mm": 500},
    )
    assert fits is True
    assert reason is None

    # Exact sum of sides boundary: 500 + 400 + 300 = 1200 mm
    fits, reason = check_dimension_limits(
        packed_weight_g=4999,
        l_mm=500,
        w_mm=400,
        h_mm=300,
        weight_max_g=5000,
        dim_rule={"max_sum_of_sides_mm": 1200, "max_single_side_mm": 500},
    )
    assert fits is True
    assert reason is None

    # Exact longest side boundary: 500 mm
    fits, reason = check_dimension_limits(
        packed_weight_g=2000,
        l_mm=500,
        w_mm=100,
        h_mm=100,
        weight_max_g=5000,
        dim_rule={"max_single_side_mm": 500},
    )
    assert fits is True
    assert reason is None


def test_boundary_values_exceeded_by_one_unit_rejected():
    """Verify exceeding by 1 gram or 1 millimeter is strictly rejected."""
    # 5001g > 5000g
    fits, reason = check_dimension_limits(
        packed_weight_g=5001,
        l_mm=200,
        w_mm=200,
        h_mm=200,
        weight_max_g=5000,
    )
    assert fits is False
    assert "exceeds maximum allowed 5000g" in reason

    # 1201mm > 1200mm sum of sides
    fits, reason = check_dimension_limits(
        packed_weight_g=4000,
        l_mm=501,
        w_mm=400,
        h_mm=300,
        weight_max_g=5000,
        dim_rule={"max_sum_of_sides_mm": 1200},
    )
    assert fits is False
    assert "Sum of sides 1201mm exceeds maximum" in reason

    # 501mm > 500mm longest side
    fits, reason = check_dimension_limits(
        packed_weight_g=2000,
        l_mm=501,
        w_mm=100,
        h_mm=100,
        weight_max_g=5000,
        dim_rule={"max_single_side_mm": 500},
    )
    assert fits is False
    assert "Longest side 501mm exceeds maximum" in reason
