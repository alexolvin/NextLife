from decimal import Decimal

from nextlife.research.normalize_price import (
    generate_comparative_market_report,
    normalize_competitor_listing,
)


def test_normalize_competitor_shipping_included_vs_excluded():
    """Verify shipping is added only when not included."""
    # Listing A: 10,000 CZK without shipping -> normalized buyer total = 10,079 CZK
    norm_a = normalize_competitor_listing(
        raw_price=Decimal("10000.00"),
        shipping_included=False,
        benchmark_shipping=Decimal("79.00"),
    )
    assert norm_a.buyer_total_price == Decimal("10079.00")
    assert norm_a.effective_shipping_cost == Decimal("79.00")

    # Listing B: 10,000 CZK with shipping included -> buyer total = 10,000 CZK
    norm_b = normalize_competitor_listing(
        raw_price=Decimal("10000.00"),
        shipping_included=True,
        benchmark_shipping=Decimal("79.00"),
    )
    assert norm_b.buyer_total_price == Decimal("10000.00")
    assert norm_b.effective_shipping_cost == Decimal("0.00")


def test_market_report_explicit_comparison_basis():
    """DoD: Market comparison report explicitly states the comparison metric."""
    competitors = [
        {"id": "1", "price": Decimal("9000.00"), "shipping_included": False},
        {"id": "2", "price": Decimal("9100.00"), "shipping_included": True},
        {"id": "3", "price": Decimal("9500.00"), "shipping_included": False},
        {"id": "4", "price": Decimal("9200.00"), "shipping_included": True},
        {"id": "5", "price": Decimal("9400.00"), "shipping_included": False},
    ]

    report = generate_comparative_market_report(competitors, min_comparables=5)

    # 1. Explicit comparison basis
    assert "Итоговая цена для покупателя" in report.comparison_basis
    assert "с учётом доставки" in report.comparison_basis

    # 2. Stats are computed from normalized buyer total prices
    assert report.stats["sufficient_data"] is True
    assert "median" in report.stats
    # All 5 items normalized
    assert len(report.normalized_prices) == 5
