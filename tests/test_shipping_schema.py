from decimal import Decimal

import pytest
import yaml

from nextlife.shipping.schema import (
    BrokerConfig,
    ShippingValidationError,
    load_and_validate_broker_file,
    load_and_validate_carrier_file,
)

# --- source PDF paths (repo-relative; resolved by the validator via the repo root) ---
ZAS = "config/shipping/_sources/zasilkovna_mezi_nami_2026-09-09.pdf"
BAL = "config/shipping/_sources/balikovna_lide_2026-09-09.pdf"
PPL = "config/shipping/_sources/ppl_lide_2026-09-09.pdf"
GLS = "config/shipping/_sources/gls_ebalik_2026-09-09.pdf"


def _anchor(section: str, row: str, column: int) -> dict:
    return {"pdf_section": section, "pdf_row": row, "pdf_column": column}


def _svc(name, price, countries, price_pdf, country_pricing_pdf, source_file, page=1) -> dict:
    return {
        "name": name,
        "service_type": "výdejní místo",
        "weight_max_g": 5000,
        "price": price,
        "country_pricing": countries,
        "price_pdf": price_pdf,
        "country_pricing_pdf": country_pricing_pdf,
        "source_url": "https://example.com/cenik",
        "fetched_at": "2026-09-09",
        "source_file": source_file,
        "source_page": page,
    }


def _write_carrier(tmp_path, name, services, carrier="Packeta"):
    path = tmp_path / f"{name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {"carrier": carrier, "display_name": "Test", "status": "ACTIVE", "services": services},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_carrier_service_missing_source_url(tmp_path):
    """Verify carrier config without source_url raises ShippingValidationError naming file, service and field."""
    conf_file = tmp_path / "packeta_bad.yaml"
    bad_yaml = """
carrier: Packeta
display_name: Zásilkovna / Packeta
services:
  - name: z_point
    service_type: "выдачное место"
    weight_max_g: 5000
    price: 79.00
    fetched_at: 2026-09-01
"""
    conf_file.write_text(bad_yaml, encoding="utf-8")

    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(conf_file)

    msg = str(exc_info.value)
    assert "packeta_bad.yaml" in msg
    assert "z_point" in msg
    assert "source_url" in msg


def test_carrier_service_missing_fetched_at(tmp_path):
    """Verify carrier config without fetched_at raises ShippingValidationError."""
    conf_file = tmp_path / "balikovna_bad.yaml"
    bad_yaml = """
carrier: Balíkovna
display_name: Balíkovna
services:
  - name: balikovna_box
    service_type: "Z-BOX"
    weight_max_g: 15000
    price: 65.00
    source_url: "https://www.balikovna.cz/cenik"
"""
    conf_file.write_text(bad_yaml, encoding="utf-8")

    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(conf_file)

    msg = str(exc_info.value)
    assert "balikovna_bad.yaml" in msg
    assert "balikovna_box" in msg
    assert "fetched_at" in msg


def test_carrier_service_missing_source_file(tmp_path):
    """Verify carrier config without source_file raises ShippingValidationError."""
    conf_file = tmp_path / "packeta_bad_source_file.yaml"
    bad_yaml = """
carrier: Packeta
display_name: Zásilkovna / Packeta
services:
  - name: z_point
    service_type: "výdejní místo"
    weight_max_g: 5000
    price: 89.00
    source_url: "https://www.zasilkovna.cz/cenik"
    fetched_at: 2026-09-09
    source_page: 1
"""
    conf_file.write_text(bad_yaml, encoding="utf-8")
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(conf_file)
    msg = str(exc_info.value)
    assert "source_file" in msg


def test_carrier_service_nonexistent_source_file(tmp_path):
    """Verify carrier config with nonexistent source_file raises ShippingValidationError."""
    conf_file = tmp_path / "packeta_missing_pdf.yaml"
    bad_yaml = """
carrier: Packeta
display_name: Zásilkovna / Packeta
services:
  - name: z_point
    service_type: "výdejní místo"
    weight_max_g: 5000
    price: 89.00
    source_url: "https://www.zasilkovna.cz/cenik"
    fetched_at: 2026-09-09
    source_file: "config/shipping/_sources/nonexistent_file.pdf"
    source_page: 1
"""
    conf_file.write_text(bad_yaml, encoding="utf-8")
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(conf_file)
    msg = str(exc_info.value)
    assert "does not exist on disk" in msg


def test_base_price_mismatch_with_pdf_fails(tmp_path):
    """Base price differing from the PDF table-cell (addressed by its anchor) raises."""
    a = _anchor("Doručení na výdejní místa a do Z-BOXů", "Česká republika", 0)  # -> 89
    svc = _svc("z_point", 9999.00, {"CZ": 89.00}, a, {"CZ": a}, ZAS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "base_mismatch", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "9999" in msg


def test_production_carrier_yamls_validated_against_source_pdfs():
    """Verify all production carrier configuration files strictly match their source PDFs."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    shipping_dir = root / "config" / "shipping"

    active_carriers = ["zasilkovna.yaml", "balikovna.yaml", "cpost.yaml", "ppl.yaml", "gls.yaml"]
    for y in active_carriers:
        yaml_path = shipping_dir / y
        assert yaml_path.exists(), f"Missing carrier config: {yaml_path}"
        cfg = load_and_validate_carrier_file(yaml_path)
        assert cfg.status == "ACTIVE"
        assert len(cfg.services) > 0
        for s in cfg.services:
            assert s.price > 0
            assert s.source_file
            assert s.source_page >= 1
            assert s.size_class is not None
            assert s.country_pricing
            assert "CZ" in s.country_pricing

    # Verify DPD is properly registered as BLOCKED-EXTERNAL
    dpd_cfg = load_and_validate_carrier_file(shipping_dir / "dpd.yaml")
    assert dpd_cfg.status == "BLOCKED-EXTERNAL"
    assert dpd_cfg.blocked_reason is not None and len(dpd_cfg.blocked_reason.strip()) > 20
    assert len(dpd_cfg.services) == 0


def test_zasilkovna_yaml_exact_specifications():
    """Verify Zásilkovna specific requirements: 129 Kč address, 149/179 nadrozměrná, SK/HU/RO, Z-BOX dims."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cfg = load_and_validate_carrier_file(root / "config" / "shipping" / "zasilkovna.yaml")
    services = {s.name: s for s in cfg.services}

    # VM Standard
    vm_std = services["zasilkovna_vydejni_misto_standard"]
    assert vm_std.price == Decimal("89.00")
    assert vm_std.country_pricing["SK"] == Decimal("99.00")
    assert vm_std.country_pricing["HU"] == Decimal("109.00")
    assert vm_std.country_pricing["RO"] == Decimal("119.00")
    assert vm_std.size_class.box_max_dimensions_cm == [35.0, 43.0, 60.0]
    assert vm_std.size_class.max_weight_kg == Decimal("5.0")
    assert vm_std.size_class.max_longest_side_cm == Decimal("60.0")
    assert vm_std.size_class.max_sum_sides_cm == Decimal("120.0")

    # VM Nadrozmerna
    vm_nad = services["zasilkovna_vydejni_misto_nadrozmer"]
    assert vm_nad.price == Decimal("149.00")
    assert vm_nad.size_class.name == "nadrozmerna"
    assert vm_nad.size_class.max_weight_kg == Decimal("15.0")
    assert vm_nad.size_class.max_longest_side_cm == Decimal("120.0")
    assert vm_nad.size_class.max_sum_sides_cm == Decimal("150.0")

    # Na adresu Standard
    adr_std = services["zasilkovna_na_adresu_standard"]
    assert adr_std.price == Decimal("129.00")
    assert adr_std.country_pricing["SK"] == Decimal("169.00")
    assert adr_std.country_pricing["HU"] == Decimal("199.00")
    assert adr_std.country_pricing["RO"] == Decimal("209.00")

    # Na adresu Nadrozmerna
    adr_nad = services["zasilkovna_na_adresu_nadrozmer"]
    assert adr_nad.price == Decimal("179.00")
    assert adr_nad.country_pricing["SK"] == Decimal("219.00")
    assert adr_nad.country_pricing["HU"] == Decimal("249.00")
    assert adr_nad.country_pricing["RO"] == Decimal("259.00")

    for s in cfg.services:
        assert s.dobirka_fee == Decimal("26.00")
        assert s.dobirka_limit == Decimal("10000.00")


def test_balikovna_yaml_exact_specifications():
    """Verify Balíkovna requirements on Page 1: 79 / 109 Kč and unregistered surcharge +10 Kč."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cfg = load_and_validate_carrier_file(root / "config" / "shipping" / "balikovna.yaml")
    services = {s.name: s for s in cfg.services}

    vm = services["balikovna_vydejni_misto_a_boxy"]
    assert vm.source_page == 1
    assert vm.price == Decimal("79.00")
    assert vm.unregistered_surcharge == Decimal("10.00")
    assert vm.dobirka_fee == Decimal("19.00")
    assert vm.insurance_limit == Decimal("50000.00")

    adr = services["balikovna_na_adresu"]
    assert adr.source_page == 1
    assert adr.price == Decimal("109.00")
    assert adr.unregistered_surcharge == Decimal("10.00")
    assert adr.dobirka_fee == Decimal("19.00")


def test_blocked_external_without_reason_fails(tmp_path):
    """Verify carrier with BLOCKED-EXTERNAL status requires blocked_reason."""
    conf_file = tmp_path / "dpd_no_reason.yaml"
    bad_yaml = """
carrier: DPD
display_name: "DPD CZ"
status: "BLOCKED-EXTERNAL"
"""
    conf_file.write_text(bad_yaml, encoding="utf-8")
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(conf_file)
    assert "blocked_reason" in str(exc_info.value)


def test_country_price_mismatch_with_pdf_fails(tmp_path):
    """A country price differing from its PDF table-cell raises, naming the country."""
    cz = _anchor("Doručení na výdejní místa a do Z-BOXů", "Česká republika", 0)  # -> 89
    sk = _anchor("Doručení na výdejní místa a do Z-BOXů", "Slovensko", 0)  # -> 99
    svc = _svc("z_point", 89.00, {"CZ": 89.00, "SK": 9999.00}, cz, {"CZ": cz, "SK": sk}, ZAS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "country_mismatch", [svc]))
    msg = str(exc_info.value)
    assert "country 'SK' price" in msg
    assert "9999" in msg
    assert "does not match source PDF" in msg


def test_delivera_broker_file_validated():
    """Verify Delivera broker configuration meets all requirements and is BLOCKED-EXTERNAL."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    delivera_path = root / "config" / "shipping" / "delivera.yaml"
    assert delivera_path.exists(), "Missing delivera.yaml broker config"

    cfg = load_and_validate_broker_file(delivera_path)
    assert isinstance(cfg, BrokerConfig)
    assert cfg.broker == "delivera"
    assert cfg.display_name == "Delivera.cz"
    assert cfg.status == "BLOCKED-EXTERNAL"
    assert cfg.blocked_reason is not None and "не публикует официальный статичный PDF-прейскурант" in cfg.blocked_reason
    assert cfg.sender_on_documents == "BROKER"
    assert cfg.cod_payout_destination == "BROKER_ACCOUNT"
    assert cfg.claims_handler == "BROKER"
    assert len(cfg.services) == 0


def test_broker_blocked_external_without_reason_fails(tmp_path):
    """Verify broker with BLOCKED-EXTERNAL status requires non-empty blocked_reason."""
    conf_file = tmp_path / "delivera_no_reason.yaml"
    bad_yaml = """
broker: delivera
display_name: "Delivera.cz"
status: "BLOCKED-EXTERNAL"
"""
    conf_file.write_text(bad_yaml, encoding="utf-8")
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_broker_file(conf_file)
    assert "blocked_reason" in str(exc_info.value)


def test_broker_active_service_requires_source_discipline(tmp_path):
    """Verify active broker service must follow strict source_file, source_url, fetched_at, source_page discipline."""
    conf_file = tmp_path / "delivera_active_bad.yaml"
    bad_yaml = """
broker: delivera
display_name: "Delivera.cz"
status: ACTIVE
services:
  - name: delivera_ppl
    service_type: "výdejní místo"
    weight_max_g: 10000
    price: 69.00
    source_url: "https://delivera.cz/cenik"
"""
    conf_file.write_text(bad_yaml, encoding="utf-8")
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_broker_file(conf_file)
    assert "missing mandatory field 'fetched_at'" in str(exc_info.value)


# --- Mandatory negative tests: one column-swap + one foreign-section price per source PDF ---
# Each case anchors a real, correctly-located price to the WRONG cell (adjacent column or a
# different section); the table-cell algorithm must detect the mismatch and raise.

def test_zasilkovna_column_swap_fails(tmp_path):
    """Standard price 89 anchored to the oversize column (149) must fail."""
    nad = _anchor("Doručení na výdejní místa a do Z-BOXů", "Česká republika", 1)  # -> 149
    svc = _svc("z_swap", 89.00, {"CZ": 89.00}, nad, {"CZ": nad}, ZAS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "z_swap", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "149" in msg


def test_zasilkovna_foreign_section_price_fails(tmp_path):
    """129 (na-adresu section) validated inside the výdejní section (89) must fail."""
    a = _anchor("Doručení na výdejní místa a do Z-BOXů", "Česká republika", 0)  # -> 89
    svc = _svc("z_foreign", 129.00, {"CZ": 129.00}, a, {"CZ": a}, ZAS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "z_foreign", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "89" in msg


def test_balikovna_column_swap_fails(tmp_path):
    """bez-dobírka 79 anchored to the s-dobírkou column (98) must fail."""
    a = _anchor("Balíkovna BEZ DOBÍRKY", "Cena", 1)  # -> 98
    svc = _svc("b_swap", 79.00, {"CZ": 79.00}, a, {"CZ": a}, BAL)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "b_swap", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "98" in msg


def test_balikovna_foreign_section_price_fails(tmp_path):
    """79 (výdejní section) validated inside the na-adresu section (109) must fail."""
    a = _anchor("Balíkovna na adresu BEZ DOBÍRKY", "Cena", 0)  # -> 109
    svc = _svc("b_foreign", 79.00, {"CZ": 79.00}, a, {"CZ": a}, BAL)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "b_foreign", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "109" in msg


def test_cpost_column_swap_fails(tmp_path):
    """S price 129 anchored to the M column (169) must fail."""
    a = _anchor("Velikostní kategorie", "Základní cena", 1)  # -> 169
    svc = _svc("c_swap", 129.00, {"CZ": 129.00}, a, {"CZ": a}, BAL, page=2)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "c_swap", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "169" in msg


def test_cpost_foreign_section_price_fails(tmp_path):
    """36 (Doplňkové služby section) validated inside the size table (129) must fail."""
    a = _anchor("Velikostní kategorie", "Základní cena", 0)  # -> 129
    svc = _svc("c_foreign", 36.00, {"CZ": 36.00}, a, {"CZ": a}, BAL, page=2)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "c_foreign", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "129" in msg


def test_gls_column_swap_fails(tmp_path):
    """na-adresu 94 anchored to the výdejní column (69) must fail."""
    a = _anchor("Přepravné v rámci České republiky", "S max. 35 cm", 0)  # -> 69
    svc = _svc("g_swap", 94.00, {"CZ": 94.00}, a, {"CZ": a}, GLS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "g_swap", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "69" in msg


def test_gls_foreign_section_price_fails(tmp_path):
    """45 (Dobírka service section) validated inside the price table (94) must fail."""
    a = _anchor("Přepravné v rámci České republiky", "S max. 35 cm", 1)  # -> 94
    svc = _svc("g_foreign", 45.00, {"CZ": 45.00}, a, {"CZ": a}, GLS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "g_foreign", [svc]))
    msg = str(exc_info.value)
    assert "does not match source PDF" in msg
    assert "94" in msg


def test_ppl_block_swap_fails(tmp_path):
    """106 (na-adresu block) and 76 (Parcelshop block) swapped between blocks must fail."""
    na_adresu = _anchor("PPL PARCEL FOR YOU CZ", "S (30 × 30 × 30 cm)", 0)  # -> 106
    parcelshop = _anchor("Parcelshop", "S (30 × 30 × 30 cm)", 0)  # -> 76
    # 106 (na-adresu value) anchored into the parcelshop block -> PDF has 76 -> mismatch
    s1 = _svc("ppl_na106_at_shop", 106.00, {"CZ": 106.00}, parcelshop, {"CZ": parcelshop}, PPL)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "ppl_na106", [s1], carrier="PPL"))
    assert "76" in str(exc_info.value)
    # 76 (parcelshop value) anchored into the na-adresu block -> PDF has 106 -> mismatch
    s2 = _svc("ppl_shop76_at_adresu", 76.00, {"CZ": 76.00}, na_adresu, {"CZ": na_adresu}, PPL)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "ppl_shop76", [s2], carrier="PPL"))
    assert "106" in str(exc_info.value)


def test_ppl_section_not_unique_fails(tmp_path):
    """A section occurring twice (PPL 'Rozměry Cena') must fail the uniqueness rule."""
    dup = _anchor("Rozměry Cena", "S (30 × 30 × 30 cm)", 0)
    svc = _svc("ppl_dup", 106.00, {"CZ": 106.00}, dup, {"CZ": dup}, PPL)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "ppl_dup", [svc], carrier="PPL"))
    assert "must occur exactly once" in str(exc_info.value)


def test_row_not_found_after_section_fails(tmp_path):
    """A row label absent after the section line must fail."""
    a = _anchor("Doručení na výdejní místa a do Z-BOXů", "Falknlandy", 0)
    svc = _svc("z_norow", 89.00, {"CZ": 89.00}, a, {"CZ": a}, ZAS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "z_norow", [svc]))
    assert "not found after pdf_section" in str(exc_info.value)


def test_missing_price_pdf_anchor_fails(tmp_path):
    """An active service without a price_pdf anchor must fail (price cannot be located)."""
    svc = _svc("z_noanchor", 89.00, {"CZ": 89.00}, None, None, ZAS)
    with pytest.raises(ShippingValidationError) as exc_info:
        load_and_validate_carrier_file(_write_carrier(tmp_path, "z_noanchor", [svc]))
    assert "missing mandatory 'price_pdf' anchor" in str(exc_info.value)

