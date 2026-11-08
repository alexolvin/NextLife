from __future__ import annotations

import pytest

from nextlife.services.kit_pc_profile import (
    _extract_ram_capacity_and_type,
    _extract_storage_capacity,
    decompose_pc_build,
    is_pc_build_candidate,
)
from nextlife.services.kit_analyzer import decompose_kit_items


def test_is_pc_build_candidate():
    assert is_pc_build_candidate("Herni pc ryzen 5 Rtx 3090 32G rezervace", "") is True
    assert is_pc_build_candidate("Gaming PC i7 12700K RTX 3080", "") is True
    assert is_pc_build_candidate("PC sestava Ryzen 5 5600 16GB RAM", "") is True
    assert is_pc_build_candidate("Počítač Dell Optiplex i5", "") is True
    assert is_pc_build_candidate("Apple iPhone 13 128GB", "") is False
    assert is_pc_build_candidate("PlayStation 5 Disc Edition", "") is False


def test_extract_ram_variations():
    # Target candidate exact title: "Herni pc ryzen 5 Rtx 3090 32G rezervace"
    ram, ddr = _extract_ram_capacity_and_type("Herni pc ryzen 5 Rtx 3090 32G rezervace")
    assert ram == 32
    assert "DDR" in ddr

    # 16G, 64G, ddr5 variations
    ram2, ddr2 = _extract_ram_capacity_and_type("Herni PC i5 16G DDR4")
    assert ram2 == 16
    assert ddr2 == "DDR4"

    ram3, ddr3 = _extract_ram_capacity_and_type("Gaming PC Ryzen 7 7800X3D 32GB DDR5")
    assert ram3 == 32
    assert ddr3 == "DDR5"

    ram4, ddr4 = _extract_ram_capacity_and_type("PC sestava 64 gb ram")
    assert ram4 == 64

    # Multi-stick 2x16gb
    ram5, _ = _extract_ram_capacity_and_type("Herni PC Ryzen 5 2x16gb")
    assert ram5 == 32


def test_extract_storage():
    name1, price1 = _extract_storage_capacity("PC 2TB SSD NVMe")
    assert "2TB" in name1
    assert price1 > 1500

    name2, price2 = _extract_storage_capacity("Gaming PC 512GB")
    assert "512GB" in name2


def test_decompose_pc_build_components():
    title = "Herni pc ryzen 5 Rtx 3090 32G rezervace"
    components = decompose_pc_build(title, "")

    component_names = [c["name"] for c in components]
    component_types = [c.get("component_type") for c in components]

    # Must contain RAM with 32GB
    assert any("RAM" in name or "Оперативная память" in name for name in component_names)
    assert "ram" in component_types
    ram_comp = next(c for c in components if c.get("component_type") == "ram")
    assert "32GB" in ram_comp["name"]
    assert ram_comp["median_price"] > 0

    # Must contain GPU RTX 3090
    assert any("3090" in name for name in component_names)
    assert "gpu" in component_types

    # Must contain CPU Ryzen 5
    assert any("Ryzen 5" in name for name in component_names)
    assert "cpu" in component_types

    # Must contain Platform (Motherboard/PSU/Case)
    assert "platform" in component_types

    # Every component must have a positive median price
    for c in components:
        assert c["median_price"] > 0


def test_kit_analyzer_delegates_to_pc_profile():
    title = "Herni pc ryzen 5 Rtx 3090 32G rezervace"
    components = decompose_kit_items(title, "")

    # Ensure RAM is present in decomposing the kit
    ram_items = [c for c in components if c.get("component_type") == "ram"]
    assert len(ram_items) == 1
    assert "32GB" in ram_items[0]["name"]
