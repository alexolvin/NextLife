"""Competitor listing price normalization for fair market comparison (TZ v2.0 Section III.2.3)."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from nextlife.services.research import calculate_price_stats

DEFAULT_BENCHMARK_SHIPPING_CZK = Decimal("79.00")


class NormalizedListingPrice(BaseModel):
    listing_id: str | None = None
    title: str | None = None
    raw_price: Decimal
    currency: str = "CZK"
    shipping_included: bool
    effective_shipping_cost: Decimal
    buyer_total_price: Decimal
    comparison_basis: str = "BUYER_TOTAL_PRICE"


class MarketComparisonReport(BaseModel):
    comparison_basis: str = Field(
        default="Итоговая цена для покупателя (с учётом доставки)",
        description="Explicit statement of the comparison metric",
    )
    metric_explanation: str = Field(
        default="Все цены конкурентов приведены к итоговой стоимости для покупателя: если доставка не включена, добавлена базовая стоимость доставки.",
    )
    sample_count: int
    normalized_prices: list[NormalizedListingPrice] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


def normalize_competitor_listing(
    raw_price: Decimal | float | int,
    currency: str = "CZK",
    shipping_included: bool = False,
    benchmark_shipping: Decimal = DEFAULT_BENCHMARK_SHIPPING_CZK,
    listing_id: str | None = None,
    title: str | None = None,
) -> NormalizedListingPrice:
    """Normalize a competitor's price to buyer total price.

    If shipping is already included, buyer total equals raw_price.
    If shipping is not included, benchmark shipping is added so comparison is apple-to-apple.
    """
    price_dec = Decimal(str(raw_price))
    shipping_dec = Decimal("0.00") if shipping_included else Decimal(str(benchmark_shipping))
    buyer_total = price_dec + shipping_dec

    return NormalizedListingPrice(
        listing_id=listing_id,
        title=title,
        raw_price=price_dec,
        currency=currency,
        shipping_included=shipping_included,
        effective_shipping_cost=shipping_dec,
        buyer_total_price=buyer_total,
        comparison_basis="BUYER_TOTAL_PRICE",
    )


def generate_comparative_market_report(
    competitor_items: list[dict[str, Any]],
    benchmark_shipping: Decimal = DEFAULT_BENCHMARK_SHIPPING_CZK,
    min_comparables: int = 5,
) -> MarketComparisonReport:
    """Compile market intelligence report comparing listings strictly by buyer total price.

    DoD: Report explicitly and clearly states which metric is being compared.
    """
    normalized_list: list[NormalizedListingPrice] = []
    totals: list[Decimal] = []

    for item in competitor_items:
        norm = normalize_competitor_listing(
            raw_price=item["price"],
            currency=item.get("currency", "CZK"),
            shipping_included=item.get("shipping_included", False),
            benchmark_shipping=benchmark_shipping,
            listing_id=item.get("id"),
            title=item.get("title"),
        )
        normalized_list.append(norm)
        totals.append(norm.buyer_total_price)

    stats = calculate_price_stats(totals, min_comparables=min_comparables)

    return MarketComparisonReport(
        comparison_basis="Итоговая цена для покупателя (с учётом доставки)",
        sample_count=len(normalized_list),
        normalized_prices=normalized_list,
        stats=stats,
    )
