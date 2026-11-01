from decimal import Decimal

import pytest

from nextlife.pricing.buyer_total import (
    UnknownTariffError,
    calculate_all_buyer_totals,
    calculate_buyer_option,
)
from nextlife.pricing.models import ShippingPolicy


@pytest.fixture
def standard_options():
    return [
        {
            "carrier_key": "packeta",
            "service_name": "Zásilkovna na výdejní místo",
            "price": Decimal("79.00"),
            "cod_supported": True,
            "cod_fee": Decimal("23.00"),
            "is_pickup": False,
        },
        {
            "carrier_key": "balikovna",
            "service_name": "Balíkovna do boxu",
            "price": Decimal("65.00"),
            "cod_supported": False,
            "cod_fee": Decimal("0.0"),
            "is_pickup": False,
        },
        {
            "carrier_key": "personal",
            "service_name": "Osobní odběr Praha",
            "price": Decimal("0.00"),
            "cod_supported": False,
            "cod_fee": Decimal("0.0"),
            "is_pickup": True,
        },
    ]


def test_three_policies_produce_three_different_correct_results(standard_options):
    """DoD: Three policies yield three distinctly calculated, correct sets of buyer options."""
    price = Decimal("1000.00")

    # 1. BUYER_PAYS: Buyer pays item + shipping
    res_buyer_pays = calculate_all_buyer_totals(price, ShippingPolicy.BUYER_PAYS, standard_options)
    lines_bp = res_buyer_pays.options
    assert len(lines_bp) == 3
    # Zásilkovna: 1000 + 79 = 1079 prepaid; 1079 + 23 = 1102 COD
    z_bp = [opt for opt in lines_bp if opt.carrier_key == "packeta"][0]
    assert z_bp.delivery_cost_buyer == Decimal("79.00")
    assert z_bp.prepaid_total == Decimal("1079.00")
    assert z_bp.cod_total == Decimal("1102.00")

    # 2. INCLUDED: Shipping included in price (delivery_cost_buyer == 0)
    res_included = calculate_all_buyer_totals(price, ShippingPolicy.INCLUDED, standard_options)
    lines_inc = res_included.options
    assert len(lines_inc) == 3
    z_inc = [opt for opt in lines_inc if opt.carrier_key == "packeta"][0]
    assert z_inc.delivery_cost_buyer == Decimal("0.00")
    assert z_inc.prepaid_total == Decimal("1000.00")
    assert z_inc.cod_total == Decimal("1023.00")
    assert "Poštovné v ceně" in z_inc.summary_text_cs

    # 3. PICKUP_ONLY: Only personal pickup allowed
    res_pickup = calculate_all_buyer_totals(price, ShippingPolicy.PICKUP_ONLY, standard_options)
    lines_pick = res_pickup.options
    assert len(lines_pick) == 1
    assert lines_pick[0].is_pickup is True
    assert lines_pick[0].prepaid_total == Decimal("1000.00")
    assert lines_pick[0].delivery_cost_buyer == Decimal("0.00")


def test_unknown_tariff_strictly_raises_error():
    """DoD: No line can ever be displayed without a verified tariff."""
    with pytest.raises(UnknownTariffError) as exc_info:
        calculate_buyer_option(
            price_gross=Decimal("500.00"),
            policy=ShippingPolicy.BUYER_PAYS,
            carrier_key="packeta",
            service_name="Z-BOX",
            delivery_tariff=None,  # Unknown tariff!
        )
    assert "without a verified tariff" in str(exc_info.value)
