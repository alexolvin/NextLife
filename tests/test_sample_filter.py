"""Unit tests for sample relevance filter (TZ §8.2, §5.2)."""

from decimal import Decimal

from nextlife.services.sample_filter import (
    calculate_sample_median,
    filter_relevant_samples,
    is_relevant_sample,
)


def test_buying_demand_excluded():
    is_ok, reason = is_relevant_sample("RTX 3090", "Koupím rtx 3090", Decimal("22000"))
    assert not is_ok
    assert reason == "buying_demand_or_trade"

    is_ok2, reason2 = is_relevant_sample("RTX 3090", "vyměním/koupím RTX 3090 24GB za jinou GPU", Decimal("20000"))
    assert not is_ok2
    assert reason2 == "buying_demand_or_trade"


def test_accessory_and_box_excluded_for_gpu():
    is_ok, reason = is_relevant_sample("RTX 3090", "Fullcover vodni blok BS-MSG3090M-PA, MSI RTX 3090", Decimal("1500"))
    assert not is_ok
    assert reason == "accessory_or_box_only"

    is_ok2, reason2 = is_relevant_sample("RTX 3090", "Pouze krabice od RTX 3090", Decimal("400"))
    assert not is_ok2
    assert reason2 == "accessory_or_box_only"


def test_complete_pc_excluded_when_searching_component():
    is_ok, reason = is_relevant_sample("RTX 3090", "Herní PC s RTX 3090", Decimal("57990"))
    assert not is_ok
    assert reason == "complete_pc_build"

    is_ok2, reason2 = is_relevant_sample("11900KF", "Herní pc setup i9 11900kf", Decimal("44990"))
    assert not is_ok2
    assert reason2 == "complete_pc_build"

    is_ok3, reason3 = is_relevant_sample("Ddr4 32", "Herní Ryzen 7 5800X3D, 32GB, 1TB SSD, RTX3080", Decimal("32999"))
    assert not is_ok3
    assert reason3 in ("complete_pc_build", "missing_core_token")


def test_defective_goods_excluded():
    is_ok, reason = is_relevant_sample("RTX 3090", "MSI RTX 3090 vadná na díly", Decimal("4000"))
    assert not is_ok
    assert reason == "defective_or_for_parts"


def test_fake_placeholder_prices_excluded():
    is_ok, reason = is_relevant_sample("RTX 3090", "ZOTAC GeForce RTX 3090 Trinity OC", Decimal("1"))
    assert not is_ok
    assert reason == "invalid_or_fake_price"

    is_ok2, reason2 = is_relevant_sample("RTX 3090", "ZOTAC GeForce RTX 3090 Trinity OC", Decimal("0"))
    assert not is_ok2
    assert reason2 == "invalid_or_fake_price"


def test_unrelated_item_missing_core_token_excluded():
    is_ok, reason = is_relevant_sample("X570", "TEXTIT KULATÁ TYČ Ø65X570MM", Decimal("1711"))
    # Not an electronics motherboard / no chipset context
    is_ok2, reason2 = is_relevant_sample("X570", "Dětské kolo", Decimal("2300"))
    assert not is_ok2
    assert reason2 == "missing_core_token"


def test_valid_standalone_card_accepted():
    is_ok, reason = is_relevant_sample("RTX 3090", "ZOTAC GeForce RTX 3090 Trinity OC 24 GB", Decimal("28500"))
    assert is_ok
    assert reason == "ok"


def test_filter_and_median_calculation():
    raw_samples = [
        {"title": "Herní PC s RTX 3090", "price": 57990, "url": "http://example.com/pc"},
        {"title": "Koupím rtx 3090", "price": 22000, "url": "http://example.com/buy"},
        {"title": "Fullcover vodni blok RTX 3090", "price": 1500, "url": "http://example.com/block"},
        {"title": "ZOTAC GeForce RTX 3090 24 GB", "price": 19000, "url": "http://example.com/gpu1"},
        {"title": "MSI RTX 3090 Gaming X 24GB", "price": 18500, "url": "http://example.com/gpu2"},
        {"title": "EVGA RTX 3090 FTW3 24GB", "price": 18000, "url": "http://example.com/gpu3"},
    ]
    valid = filter_relevant_samples("RTX 3090", raw_samples)
    assert len(valid) == 3
    med, count, urls = calculate_sample_median("RTX 3090", raw_samples, fallback_median=20000)
    assert count == 3
    assert med == 18500
    assert len(urls) == 3
