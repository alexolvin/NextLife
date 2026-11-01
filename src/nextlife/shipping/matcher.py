"""Shipping service matching and eligibility engine (TZ v2.0 Section III.2.2)."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from nextlife.shipping.dimensions import check_dimension_limits
from nextlife.shipping.schema import PriceModifier, ShippingService


class MatchedService(BaseModel):
    carrier_key: str
    broker_key: str | None = None
    service_name: str
    service_type: str
    price: Decimal  # Base tariff price
    effective_price: Decimal  # Effective price after active promotions/modifiers
    currency: str
    cod_supported: bool
    cod_fee: Decimal
    insurance_included: Decimal
    insurance_limit: Decimal
    insurance_max: Decimal | None = None
    sender_on_documents: str = "SELLER"  # SELLER | BROKER
    cod_payout_destination: str = "SELLER_BANK_ACCOUNT"  # SELLER_BANK_ACCOUNT | BROKER_ACCOUNT
    claims_handler: str = "CARRIER"  # CARRIER | BROKER
    delivery_days: str | None = None
    source_url: str
    price_modifiers: list[PriceModifier] = Field(default_factory=list)


class RejectedService(BaseModel):
    carrier_key: str = "carrier"
    broker_key: str | None = None
    service_name: str
    reason: str


class MatchingResult(BaseModel):
    suitable_services: list[MatchedService] = Field(default_factory=list)
    rejected_services: list[RejectedService] = Field(default_factory=list)
    only_pickup_available: bool = False
    message: str | None = None


def match_shipping_services(
    packed_weight_g: int,
    packed_l_mm: int,
    packed_w_mm: int,
    packed_h_mm: int,
    services: list[dict[str, Any] | ShippingService],
    destination_country: str = "CZ",
    item_value: Decimal | None = None,
    require_insurance: bool = False,
) -> MatchingResult:
    """Evaluate and match suitable shipping services, sorted strictly by price ascending.

    DoD requirements:
    - If no service is suitable, returns empty list with detailed rejection reasons for each service.
    - Invented prices are constructively impossible: prices are strictly pulled from verified service.price.
    - Accurately reports broker_key, carrier_key, sender_on_documents, cod_payout_destination, claims_handler, insurance_limit.
    """
    suitable: list[MatchedService] = []
    rejected: list[RejectedService] = []

    norm_country = destination_country.upper().strip()

    for s in services:
        # Convert dict to ShippingService if needed
        if isinstance(s, dict):
            svc = ShippingService(**s)
            carrier_key = s.get("carrier_key") or getattr(svc, "carrier_key", None) or "carrier"
            broker_key = s.get("broker_key") or getattr(svc, "broker_key", None)
        else:
            svc = s
            carrier_key = getattr(s, "carrier_key", None) or getattr(s, "carrier", None) or "carrier"
            broker_key = getattr(s, "broker_key", None)

        s_name = svc.name

        # 1. Country support
        supported_countries = [c.upper() for c in svc.countries]
        if norm_country not in supported_countries:
            rejected.append(
                RejectedService(
                    carrier_key=carrier_key,
                    broker_key=broker_key,
                    service_name=s_name,
                    reason=f"Country '{norm_country}' not supported (supported: {', '.join(supported_countries)})",
                )
            )
            continue

        # 2. Weight and dimension limits
        dim_rule_dict: dict[str, Any] | None = (
            svc.dim_rule.model_dump() if hasattr(svc.dim_rule, "model_dump")
            else (svc.dim_rule if isinstance(svc.dim_rule, dict) else None)
        )
        fits, dim_reason = check_dimension_limits(
            packed_weight_g=packed_weight_g,
            l_mm=packed_l_mm,
            w_mm=packed_w_mm,
            h_mm=packed_h_mm,
            weight_max_g=svc.weight_max_g,
            dim_rule=dim_rule_dict,
        )
        if not fits:
            rejected.append(
                RejectedService(
                    carrier_key=carrier_key,
                    broker_key=broker_key,
                    service_name=s_name,
                    reason=dim_reason or "Exceeds dimensions/weight",
                )
            )
            continue

        # 3. Insurance requirement
        ins_limit = getattr(svc, "insurance_limit", svc.insurance_included)
        if require_insurance and item_value is not None:
            max_coverage = max(svc.insurance_included, ins_limit, svc.insurance_max or Decimal("0.0"))
            if max_coverage < item_value:
                rejected.append(
                    RejectedService(
                        carrier_key=carrier_key,
                        broker_key=broker_key,
                        service_name=s_name,
                        reason=f"Required insurance {item_value} {svc.currency} exceeds maximum coverage {max_coverage} {svc.currency}",
                    )
                )
                continue

        # 4. Calculate effective price with active modifiers
        base_price = svc.price
        effective_price = base_price
        for mod in getattr(svc, "price_modifiers", []):
            effective_price = mod.apply(effective_price)

        # Suitable service matched!
        suitable.append(
            MatchedService(
                carrier_key=carrier_key,
                broker_key=broker_key,
                service_name=svc.name,
                service_type=svc.service_type,
                price=base_price,
                effective_price=effective_price,
                currency=svc.currency,
                cod_supported=svc.cod_supported,
                cod_fee=svc.cod_fee,
                insurance_included=svc.insurance_included,
                insurance_limit=ins_limit,
                insurance_max=svc.insurance_max,
                sender_on_documents=getattr(svc, "sender_on_documents", "SELLER"),
                cod_payout_destination=getattr(svc, "cod_payout_destination", "SELLER_BANK_ACCOUNT"),
                claims_handler=getattr(svc, "claims_handler", "CARRIER"),
                delivery_days=str(svc.delivery_days) if svc.delivery_days else None,
                source_url=svc.source_url,
                price_modifiers=list(getattr(svc, "price_modifiers", [])),
            )
        )

    # Sort strictly by effective price ascending
    suitable.sort(key=lambda item: item.effective_price)

    only_pickup = len(suitable) == 0
    msg = None
    if only_pickup:
        msg = "Предмет слишком тяжёлый или крупный для подключённых перевозчиков, доступен только osobní odběr."

    return MatchingResult(
        suitable_services=suitable,
        rejected_services=rejected,
        only_pickup_available=only_pickup,
        message=msg,
    )
