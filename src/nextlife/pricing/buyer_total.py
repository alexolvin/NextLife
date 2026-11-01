"""Buyer total calculation per delivery option and shipping policy (TZ v2.0 Section III.2.3)."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from nextlife.core.exceptions import NextLifeError
from nextlife.pricing.models import ShippingPolicy


class UnknownTariffError(NextLifeError):
    """Raised when an attempt is made to calculate buyer total without a verified tariff price."""


class BuyerOptionLine(BaseModel):
    carrier_key: str
    service_name: str
    is_pickup: bool = False
    delivery_tariff: Decimal
    delivery_cost_buyer: Decimal
    item_price: Decimal
    prepaid_total: Decimal
    cod_supported: bool
    cod_fee: Decimal = Decimal("0.0")
    cod_total: Decimal | None = None
    summary_text_cs: str


class BuyerTotalsResult(BaseModel):
    policy: ShippingPolicy
    item_price_gross: Decimal
    options: list[BuyerOptionLine] = Field(default_factory=list)


def calculate_buyer_option(
    price_gross: Decimal,
    policy: ShippingPolicy | str,
    carrier_key: str,
    service_name: str,
    delivery_tariff: Decimal | None,
    cod_supported: bool = False,
    cod_fee: Decimal = Decimal("0.0"),
    is_pickup: bool = False,
) -> BuyerOptionLine:
    """Calculate single buyer option line under given policy and verified tariff.

    DoD: No line is generated without a known verified tariff.
    """
    if delivery_tariff is None and not is_pickup:
        raise UnknownTariffError(
            f"Cannot calculate buyer option for '{service_name}' ({carrier_key}) without a verified tariff price."
        )

    tariff = Decimal("0.00") if is_pickup else Decimal(str(delivery_tariff))
    policy_enum = ShippingPolicy(policy)

    if policy_enum == ShippingPolicy.PICKUP_ONLY:
        if not is_pickup:
            raise UnknownTariffError("Under PICKUP_ONLY policy, carrier shipping options are prohibited.")
        delivery_buyer = Decimal("0.00")
    elif policy_enum == ShippingPolicy.INCLUDED:
        delivery_buyer = Decimal("0.00")
    elif policy_enum == ShippingPolicy.BUYER_PAYS:
        delivery_buyer = tariff
    else:
        delivery_buyer = tariff

    prepaid_total = price_gross + delivery_buyer

    cod_total = None
    if cod_supported and not is_pickup:
        cod_total = prepaid_total + cod_fee

    # Czech summary string for listing
    if is_pickup:
        summary_cs = f"Osobní odběr (Praha): Zdarma (platba hotově / převodem na místě: {prepaid_total:.0f} Kč)"
    elif policy_enum == ShippingPolicy.INCLUDED:
        if cod_supported and cod_total is not None:
            summary_cs = f"{service_name}: Poštovné v ceně (převodem {prepaid_total:.0f} Kč, dobírka {cod_total:.0f} Kč)"
        else:
            summary_cs = f"{service_name}: Poštovné v ceně (celkem {prepaid_total:.0f} Kč převodem)"
    else:  # BUYER_PAYS
        if cod_supported and cod_total is not None:
            summary_cs = f"{service_name}: {delivery_buyer:.0f} Kč (převodem celkem {prepaid_total:.0f} Kč, na dobírku {cod_total:.0f} Kč)"
        else:
            summary_cs = f"{service_name}: {delivery_buyer:.0f} Kč (převodem celkem {prepaid_total:.0f} Kč)"

    return BuyerOptionLine(
        carrier_key=carrier_key,
        service_name=service_name,
        is_pickup=is_pickup,
        delivery_tariff=tariff,
        delivery_cost_buyer=delivery_buyer,
        item_price=price_gross,
        prepaid_total=prepaid_total,
        cod_supported=cod_supported and not is_pickup,
        cod_fee=cod_fee if cod_supported and not is_pickup else Decimal("0.0"),
        cod_total=cod_total,
        summary_text_cs=summary_cs,
    )


def calculate_all_buyer_totals(
    price_gross: Decimal,
    policy: ShippingPolicy | str,
    options_data: list[dict[str, Any]],
) -> BuyerTotalsResult:
    """Calculate buyer totals for all configured options under policy."""
    policy_enum = ShippingPolicy(policy)
    lines: list[BuyerOptionLine] = []

    for opt in options_data:
        is_pickup = opt.get("is_pickup", False)
        if policy_enum == ShippingPolicy.PICKUP_ONLY and not is_pickup:
            continue

        line = calculate_buyer_option(
            price_gross=price_gross,
            policy=policy_enum,
            carrier_key=opt.get("carrier_key", "carrier"),
            service_name=opt.get("service_name", "Service"),
            delivery_tariff=opt.get("price"),
            cod_supported=opt.get("cod_supported", False),
            cod_fee=opt.get("cod_fee", Decimal("0.0")),
            is_pickup=is_pickup,
        )
        lines.append(line)

    return BuyerTotalsResult(
        policy=policy_enum,
        item_price_gross=price_gross,
        options=lines,
    )
