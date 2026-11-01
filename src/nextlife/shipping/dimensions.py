"""Dimension, weight, and packaging calculation rules (TZ v2.0 Section III.2.2, III.2.4)."""

import math
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PACKAGING_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "packaging.yaml"


def calculate_sum_of_sides(l_mm: int, w_mm: int, h_mm: int) -> int:
    """Calculate the sum of three sides (L + W + H) in millimeters."""
    return int(l_mm) + int(w_mm) + int(h_mm)


def get_longest_side(l_mm: int, w_mm: int, h_mm: int) -> int:
    """Return the longest dimension in millimeters."""
    return max(int(l_mm), int(w_mm), int(h_mm))


def calculate_volumetric_weight_g(l_mm: int, w_mm: int, h_mm: int, divisor: int = 5000) -> int:
    """Calculate volumetric weight in grams.
    Formula: (L_cm * W_cm * H_cm) / divisor = kg -> * 1000 = grams.
    """
    l_cm = l_mm / 10.0
    w_cm = w_mm / 10.0
    h_cm = h_mm / 10.0
    vol_kg = (l_cm * w_cm * h_cm) / float(divisor)
    return int(math.ceil(vol_kg * 1000.0))


def apply_packaging(
    raw_weight_g: int,
    raw_l_mm: int,
    raw_w_mm: int,
    raw_h_mm: int,
    packaging_type: str = "box_small",
    config_path: Path | str | None = None,
) -> tuple[int, int, int, int]:
    """Calculate packed dimensions and weight by adding packaging materials.

    Returns:
        (packed_weight_g, packed_l_mm, packed_w_mm, packed_h_mm)
    """
    cfg_file = Path(config_path) if config_path else DEFAULT_PACKAGING_PATH
    pkg_data = {}
    if cfg_file.exists():
        try:
            loaded = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
            pkg_data = loaded.get("packaging_types", {})
        except Exception:
            pkg_data = {}

    type_info = pkg_data.get(packaging_type, {})
    weight_add = type_info.get("weight_add_g", 150)
    l_add = type_info.get("dim_add_l_mm", 30)
    w_add = type_info.get("dim_add_w_mm", 30)
    h_add = type_info.get("dim_add_h_mm", 30)

    packed_weight = raw_weight_g + weight_add
    packed_l = raw_l_mm + l_add
    packed_w = raw_w_mm + w_add
    packed_h = raw_h_mm + h_add
    return packed_weight, packed_l, packed_w, packed_h


def check_dimension_limits(
    packed_weight_g: int,
    l_mm: int,
    w_mm: int,
    h_mm: int,
    weight_max_g: int,
    dim_rule: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Check if packed parcel strictly fits within carrier service limits.

    Note: Items exactly on the boundary limit are considered acceptable (<=).
    """
    # 1. Weight limit check (<=)
    if packed_weight_g > weight_max_g:
        return False, f"Weight {packed_weight_g}g exceeds maximum allowed {weight_max_g}g"

    if not dim_rule:
        return True, None

    # 2. Sum of sides check (<=)
    max_sum = dim_rule.get("max_sum_of_sides_mm")
    if max_sum is not None:
        sum_sides = calculate_sum_of_sides(l_mm, w_mm, h_mm)
        if sum_sides > max_sum:
            return False, f"Sum of sides {sum_sides}mm exceeds maximum allowed {max_sum}mm"

    # 3. Longest side check (<=)
    max_single = dim_rule.get("max_single_side_mm")
    if max_single is not None:
        longest = get_longest_side(l_mm, w_mm, h_mm)
        if longest > max_single:
            return False, f"Longest side {longest}mm exceeds maximum allowed {max_single}mm"

    # 4. Individual box dimensions check with 3D rotation (<=)
    limit_l = dim_rule.get("max_l_mm")
    limit_w = dim_rule.get("max_w_mm")
    limit_h = dim_rule.get("max_h_mm")
    if limit_l is not None and limit_w is not None and limit_h is not None:
        item_dims = sorted([l_mm, w_mm, h_mm])
        limit_dims = sorted([limit_l, limit_w, limit_h])
        if (
            item_dims[0] > limit_dims[0]
            or item_dims[1] > limit_dims[1]
            or item_dims[2] > limit_dims[2]
        ):
            return False, f"Dimensions ({l_mm}x{w_mm}x{h_mm}) exceed box limits ({limit_l}x{limit_w}x{limit_h})"

    return True, None
