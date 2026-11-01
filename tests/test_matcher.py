from datetime import date
from decimal import Decimal

import pytest

from nextlife.shipping.matcher import match_shipping_services
from nextlife.shipping.schema import ShippingService


@pytest.fixture
def sample_services():
    return [
        ShippingService(
            name="packeta_box",
            service_type="Z-BOX",
            weight_max_g=5000,
            dim_rule={"max_sum_of_sides_mm": 1200, "max_single_side_mm": 500},
            price=Decimal("79.00"),
            currency="CZK",
            countries=["CZ", "SK"],
            insurance_included=Decimal("300.00"),
            insurance_max=Decimal("20000.00"),
            source_url="https://zasilkovna.cz/rates",
            fetched_at=date(2026, 9, 1),
            source_file="config/shipping/_sources/zasilkovna_mezi_nami_2026-09-09.pdf",
            source_page=1,
        ),
        ShippingService(
            name="balikovna_post",
            service_type="почтовое отделение",
            weight_max_g=15000,
            dim_rule={"max_single_side_mm": 700},
            price=Decimal("65.00"),
            currency="CZK",
            countries=["CZ"],
            insurance_included=Decimal("1000.00"),
            insurance_max=Decimal("30000.00"),
            source_url="https://balikovna.cz/rates",
            fetched_at=date(2026, 9, 1),
            source_file="config/shipping/_sources/balikovna_lide_2026-09-09.pdf",
            source_page=1,
        ),
        ShippingService(
            name="ppl_door",
            service_type="до двери",
            weight_max_g=30000,
            dim_rule={"max_sum_of_sides_mm": 2000},
            price=Decimal("119.00"),
            currency="CZK",
            countries=["CZ", "DE", "SK"],
            insurance_included=Decimal("50000.00"),
            insurance_max=Decimal("50000.00"),
            source_url="https://ppl.cz/rates",
            fetched_at=date(2026, 9, 1),
            source_file="config/shipping/_sources/zasilkovna_mezi_nami_2026-09-09.pdf",
            source_page=1,
        ),
    ]


def test_matcher_sorts_by_price_ascending(sample_services):
    """Verify services are strictly sorted by price ascending."""
    res = match_shipping_services(
        packed_weight_g=2000,
        packed_l_mm=300,
        packed_w_mm=200,
        packed_h_mm=100,
        services=sample_services,
        destination_country="CZ",
    )
    assert len(res.suitable_services) == 3
    # Balíkovna (65.00) < Packeta (79.00) < PPL (119.00)
    assert res.suitable_services[0].service_name == "balikovna_post"
    assert res.suitable_services[0].price == Decimal("65.00")
    assert res.suitable_services[1].service_name == "packeta_box"
    assert res.suitable_services[1].price == Decimal("79.00")
    assert res.suitable_services[2].service_name == "ppl_door"
    assert res.suitable_services[2].price == Decimal("119.00")


def test_matcher_country_filter(sample_services):
    """Verify services that do not support destination country are rejected."""
    # Destination SK: Balíkovna only supports CZ, so it must be rejected
    res = match_shipping_services(
        packed_weight_g=2000,
        packed_l_mm=300,
        packed_w_mm=200,
        packed_h_mm=100,
        services=sample_services,
        destination_country="SK",
    )
    suitable_names = [s.service_name for s in res.suitable_services]
    assert "balikovna_post" not in suitable_names
    assert "packeta_box" in suitable_names
    assert "ppl_door" in suitable_names

    # Check rejection record
    rejected = [r for r in res.rejected_services if r.service_name == "balikovna_post"]
    assert len(rejected) == 1
    assert "Country 'SK' not supported" in rejected[0].reason


def test_matcher_overweight_or_oversized_returns_empty_and_reasons(sample_services):
    """DoD: When no service fits, returns empty list, each rejection has reason, only pickup available."""
    # 35 kg exceeds all services (max is 30 kg)
    res = match_shipping_services(
        packed_weight_g=35000,
        packed_l_mm=400,
        packed_w_mm=300,
        packed_h_mm=200,
        services=sample_services,
        destination_country="CZ",
    )
    assert len(res.suitable_services) == 0
    assert res.only_pickup_available is True
    assert "доступен только osobní odběr" in res.message
    assert len(res.rejected_services) == 3
    for rej in res.rejected_services:
        assert "exceeds maximum allowed" in rej.reason


def test_matcher_insurance_requirement(sample_services):
    """Verify services with insufficient insurance coverage are rejected when insurance is required."""
    # High value item: 25,000 CZK. Packeta max is 20,000, so Packeta must be rejected.
    res = match_shipping_services(
        packed_weight_g=2000,
        packed_l_mm=300,
        packed_w_mm=200,
        packed_h_mm=100,
        services=sample_services,
        destination_country="CZ",
        item_value=Decimal("25000.00"),
        require_insurance=True,
    )
    suitable_names = [s.service_name for s in res.suitable_services]
    assert "packeta_box" not in suitable_names
    assert "balikovna_post" in suitable_names
    assert "ppl_door" in suitable_names

    packeta_rej = [r for r in res.rejected_services if r.service_name == "packeta_box"][0]
    assert "exceeds maximum coverage" in packeta_rej.reason


def test_matcher_direct_carrier_vs_broker_options():
    """Verify that the same carrier service is returned both directly and via broker, marking document metadata."""
    from nextlife.shipping.schema import PriceModifier

    direct_ppl = ShippingService(
        name="ppl_direct_parcelshop",
        carrier_key="ppl",
        broker_key=None,
        service_type="výdejní místo (PPL Direct)",
        weight_max_g=31500,
        price=Decimal("76.00"),
        currency="CZK",
        countries=["CZ"],
        sender_on_documents="SELLER",
        cod_payout_destination="SELLER_BANK_ACCOUNT",
        claims_handler="CARRIER",
        insurance_limit=Decimal("5000.00"),
        source_url="https://www.ppl.cz/cenik",
        fetched_at=date(2026, 9, 9),
        source_file="config/shipping/_sources/ppl_lide_2026-09-09.pdf",
        source_page=1,
    )

    broker_delivera_ppl = ShippingService(
        name="ppl_via_delivera",
        carrier_key="ppl",
        broker_key="delivera",
        service_type="výdejní místo (PPL via Delivera)",
        weight_max_g=31500,
        price=Decimal("69.00"),
        currency="CZK",
        countries=["CZ"],
        sender_on_documents="BROKER",
        cod_payout_destination="BROKER_ACCOUNT",
        claims_handler="BROKER",
        insurance_limit=Decimal("20000.00"),
        source_url="https://delivera.cz/cenik",
        fetched_at=date(2026, 9, 9),
        source_file="config/shipping/_sources/ppl_lide_2026-09-09.pdf",
        source_page=1,
    )

    res = match_shipping_services(
        packed_weight_g=2000,
        packed_l_mm=200,
        packed_w_mm=200,
        packed_h_mm=100,
        services=[direct_ppl, broker_delivera_ppl],
        destination_country="CZ",
    )

    assert len(res.suitable_services) == 2
    # Sorted by price ascending: Delivera broker (69.00) < Direct PPL (76.00)
    delivera_opt = res.suitable_services[0]
    direct_opt = res.suitable_services[1]

    # Check broker option
    assert delivera_opt.carrier_key == "ppl"
    assert delivera_opt.broker_key == "delivera"
    assert delivera_opt.price == Decimal("69.00")
    assert delivera_opt.effective_price == Decimal("69.00")
    assert delivera_opt.sender_on_documents == "BROKER"
    assert delivera_opt.cod_payout_destination == "BROKER_ACCOUNT"
    assert delivera_opt.claims_handler == "BROKER"
    assert delivera_opt.insurance_limit == Decimal("20000.00")

    # Check direct option
    assert direct_opt.carrier_key == "ppl"
    assert direct_opt.broker_key is None
    assert direct_opt.price == Decimal("76.00")
    assert direct_opt.effective_price == Decimal("76.00")
    assert direct_opt.sender_on_documents == "SELLER"
    assert direct_opt.cod_payout_destination == "SELLER_BANK_ACCOUNT"
    assert direct_opt.claims_handler == "CARRIER"
    assert direct_opt.insurance_limit == Decimal("5000.00")


def test_matcher_price_modifier_with_validity():
    """Verify promotional discounts are separate modifiers with validity and don't corrupt base tariff."""
    from datetime import timedelta
    from nextlife.shipping.schema import PriceModifier

    today = date.today()
    active_discount = PriceModifier(
        name="Léto 2026 Sleva",
        modifier_type="PERCENT_DISCOUNT",
        amount=Decimal("20.00"),
        valid_from=today - timedelta(days=5),
        valid_to=today + timedelta(days=5),
    )
    expired_discount = PriceModifier(
        name="Jaro 2026 Sleva",
        modifier_type="PERCENT_DISCOUNT",
        amount=Decimal("50.00"),
        valid_from=today - timedelta(days=30),
        valid_to=today - timedelta(days=5),
    )

    svc_active = ShippingService(
        name="ppl_discounted",
        carrier_key="ppl",
        broker_key="delivera",
        service_type="výdejní místo",
        weight_max_g=10000,
        price=Decimal("100.00"),
        price_modifiers=[active_discount],
        countries=["CZ"],
        source_url="https://delivera.cz/cenik",
        fetched_at=date(2026, 9, 9),
        source_file="config/shipping/_sources/ppl_lide_2026-09-09.pdf",
        source_page=1,
    )

    svc_expired = ShippingService(
        name="ppl_expired_promo",
        carrier_key="ppl",
        broker_key="delivera",
        service_type="výdejní místo",
        weight_max_g=10000,
        price=Decimal("100.00"),
        price_modifiers=[expired_discount],
        countries=["CZ"],
        source_url="https://delivera.cz/cenik",
        fetched_at=date(2026, 9, 9),
        source_file="config/shipping/_sources/ppl_lide_2026-09-09.pdf",
        source_page=1,
    )

    res = match_shipping_services(
        packed_weight_g=2000,
        packed_l_mm=200,
        packed_w_mm=200,
        packed_h_mm=100,
        services=[svc_active, svc_expired],
        destination_country="CZ",
    )

    assert len(res.suitable_services) == 2
    # Active discount: 100 - 20% = 80.00
    active_opt = next(s for s in res.suitable_services if s.service_name == "ppl_discounted")
    assert active_opt.price == Decimal("100.00")  # Base tariff unchanged
    assert active_opt.effective_price == Decimal("80.00")

    # Expired discount: remains 100.00
    expired_opt = next(s for s in res.suitable_services if s.service_name == "ppl_expired_promo")
    assert expired_opt.price == Decimal("100.00")
    assert expired_opt.effective_price == Decimal("100.00")

