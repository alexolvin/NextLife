from decimal import Decimal

from nextlife.pricing.settlement import calculate_settlement


def test_confirmed_commission_exact_settlement():
    """Verify exact net payout calculation when all terms and commissions are verified."""
    # Item: 10,000 CZK
    # Shipping received: 79 CZK
    # Actual shipping cost: 79 CZK
    # Commission: 6% Aukro commission = 600 CZK
    # COD fee: 23 CZK
    # Packaging: 30 CZK
    # Net payout: 10,000 + 79 - 79 - 600 - 23 - 30 = 9,268 CZK
    res = calculate_settlement(
        price_gross=Decimal("10000.00"),
        shipping_received=Decimal("79.00"),
        shipping_cost_actual=Decimal("79.00"),
        cod_fee_actual=Decimal("23.00"),
        packaging_cost=Decimal("30.00"),
        marketplace_commission_rate=Decimal("0.06"),
        commission_confirmed=True,
    )
    assert res.is_fully_confirmed is True
    assert res.net_payout == Decimal("9347.00")
    assert res.net_payout_min is None
    assert res.net_payout_max is None


def test_unconfirmed_commission_yields_range_and_warning_note():
    """DoD: Unknown commission does NOT turn to zero, produces a range with note 'не подтверждено'."""
    # Item: 10,000 CZK
    # Shipping: 0, Packaging: 50 CZK
    # Base funds = 9,950 CZK
    # Commission: None / unconfirmed!
    res = calculate_settlement(
        price_gross=Decimal("10000.00"),
        shipping_received=Decimal("0.00"),
        shipping_cost_actual=Decimal("0.00"),
        packaging_cost=Decimal("50.00"),
        marketplace_commission_rate=None,  # Unknown!
        commission_confirmed=False,
    )
    # Must NOT set net_payout to a single number assuming zero commission
    assert res.net_payout is None
    assert res.is_fully_confirmed is False

    # Range must be present
    assert res.net_payout_max == Decimal("9950.00")
    # Min reflects conservative estimate (e.g. 15% = 1500 -> 8450)
    assert res.net_payout_min < res.net_payout_max
    assert res.net_payout_min == Decimal("8450.00")

    # Check line has explicit 'не подтверждено' note
    comm_line = [line for line in res.lines if line.name == "Комиссия площадки"][0]
    assert comm_line.is_confirmed is False
    assert comm_line.note == "не подтверждено"
