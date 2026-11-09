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


def test_exclusions_ad_text_ground_truth():
    """Components explicitly excluded in ad text must NEVER be added."""
    # 1. PC sold without GPU
    comps_no_gpu = decompose_pc_build("Herní PC i5 12400F 16GB bez grafické karty", "prodávám bez GPU")
    types_no_gpu = [c.get("component_type") for c in comps_no_gpu]
    assert "gpu" not in types_no_gpu
    assert not any("GPU" in c["name"] or "Видеокарта" in c["name"] for c in comps_no_gpu)

    # 2. PC sold without storage
    comps_no_disk = decompose_pc_build("Herní PC Ryzen 5 5600 RTX 3070 16GB bez disku", "SSD si nechávám")
    types_no_disk = [c.get("component_type") for c in comps_no_disk]
    assert "storage" not in types_no_disk

    # 3. Upgrade set (motherboard + CPU + RAM) without case/PSU/GPU
    comps_upgrade = decompose_pc_build("Upgrade set deska ASUS B550 + Ryzen 5 5600 + 32GB RAM", "pouze komponenty")
    types_upgrade = [c.get("component_type") for c in comps_upgrade]
    assert "platform" not in types_upgrade  # No Case & PSU
    assert "gpu" not in types_upgrade       # No GPU
    assert "cpu" in types_upgrade
    assert "ram" in types_upgrade
    assert "motherboard" in types_upgrade


def test_ram_generations_frequency_and_sodimm():
    """Verify DDR2-DDR5, frequency premiums, and SODIMM form-factor."""
    from nextlife.services.kit_ram_profile import extract_ram_details, calculate_ram_price

    # DDR2
    spec_ddr2 = extract_ram_details("Starší PC 4GB DDR2 800MHz")
    assert spec_ddr2.generation == "DDR2"
    assert spec_ddr2.capacity_gb == 4
    assert spec_ddr2.frequency_mhz == 800
    p_ddr2 = calculate_ram_price(spec_ddr2)
    assert 100 <= p_ddr2 <= 200

    # DDR3 SODIMM
    spec_sodimm = extract_ram_details("Notebook RAM 16GB DDR3 SODIMM 1600MHz")
    assert spec_sodimm.generation == "DDR3"
    assert spec_sodimm.form_factor == "SODIMM"
    assert spec_sodimm.capacity_gb == 16
    p_sodimm = calculate_ram_price(spec_sodimm)
    assert 500 <= p_sodimm <= 800

    # DDR4 3600MHz (frequency bonus)
    spec_d4_base = extract_ram_details("RAM 32GB DDR4 2400MHz")
    spec_d4_fast = extract_ram_details("RAM 32GB DDR4 3600MHz")
    p_base = calculate_ram_price(spec_d4_base)
    p_fast = calculate_ram_price(spec_d4_fast)
    assert p_fast > p_base
    assert p_fast >= 1800

    # DDR5 6000MHz
    spec_d5 = extract_ram_details("Gaming PC 32GB DDR5 6000MHz CL30")
    assert spec_d5.generation == "DDR5"
    assert spec_d5.frequency_mhz == 6000
    p_d5 = calculate_ram_price(spec_d5)
    assert p_d5 >= 2500


def test_gpu_overpriced_samples_cutoff():
    """Cut off overpriced listings and whole PCs for GPU market queries."""
    from nextlife.services.sample_filter import calculate_sample_median, is_relevant_sample

    # Listing for a whole PC must NOT match a standalone GPU query
    ok, reason = is_relevant_sample("RTX 3080", "Herní PC sestava RTX 3080 i7 32GB", 28000)
    assert ok is False
    assert "pc" in reason.lower()

    # Overpriced listings cut off: baseline 10,500 CZK
    sample_items = [
        {"title": "MSI RTX 3080 Gaming X Trio", "price": 10000, "url": "https://bazos.cz/1"},
        {"title": "ASUS TUF RTX 3080 10GB", "price": 10500, "url": "https://bazos.cz/2"},
        {"title": "GIGABYTE RTX 3080 Eagle", "price": 11000, "url": "https://bazos.cz/3"},
        {"title": "Scalper RTX 3080 insane price", "price": 24000, "url": "https://bazos.cz/4"},  # Overpriced
    ]
    # Ceiling is 1.20 * 10500 = 12600 CZK
    med, count, urls = calculate_sample_median(
        query="RTX 3080",
        items=sample_items,
        fallback_median=10500,
        expected_price_range=(5250, 12600),
    )
    assert count == 3  # The 24,000 listing was cut off!
    assert med <= 11000
    assert not any("4" in u for u in urls)

