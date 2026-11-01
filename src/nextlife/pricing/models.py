"""Lot pricing data models and serialization (TZ v2.0 Section III.1)."""

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from nextlife.comms.outgoing_filter import InternalFieldLeakageError


class ShippingPolicy(StrEnum):
    BUYER_PAYS = "BUYER_PAYS"
    INCLUDED = "INCLUDED"
    PICKUP_ONLY = "PICKUP_ONLY"


class PublicLotPricingSchema(BaseModel):
    """Publicly serializable pricing representation.

    DoD: price_floor is STRICTLY excluded from all outgoing serializers.
    """
    item_code: str
    price_gross: Decimal
    price_base: Decimal | None = None
    negotiable: bool
    shipping_policy: ShippingPolicy
    shipping_included_amount: Decimal = Decimal("0.0")


class InternalLotPricingSchema(PublicLotPricingSchema):
    """Internal model containing private reserve floor."""
    price_floor: Decimal = Field(..., description="Internal reserve minimum price (internal=True)")
    price_decay_steps: list[float] = Field(default_factory=lambda: [-0.05, -0.10, -0.15])
    stale_days: int = 14

    # Marker attribute for outgoing filter inspection
    internal: bool = True


def serialize_outgoing_pricing(pricing: InternalLotPricingSchema | dict[str, Any]) -> dict[str, Any]:
    """Serialize pricing for external marketplace or customer communication.

    Guarantees price_floor is never leaked into outgoing payload.
    """
    if isinstance(pricing, dict):
        raw = dict(pricing)
    elif hasattr(pricing, "model_dump"):
        raw = pricing.model_dump()
    elif hasattr(pricing, "__dict__"):
        raw = {k: v for k, v in pricing.__dict__.items() if not k.startswith("_")}
    else:
        raw = {}

    if "price_floor" in raw:
        # Strip or raise if explicitly passed to outgoing serializer
        # Enforcing strict exclusion
        del raw["price_floor"]

    # Final check: price_floor cannot be present
    if "price_floor" in raw:
        raise InternalFieldLeakageError("Critical: price_floor could not be stripped from outgoing pricing payload!")

    # Return only public fields
    return {
        "item_code": raw.get("item_code"),
        "price_gross": raw.get("price_gross"),
        "price_base": raw.get("price_base"),
        "negotiable": raw.get("negotiable", True),
        "shipping_policy": raw.get("shipping_policy", ShippingPolicy.BUYER_PAYS.value),
        "shipping_included_amount": raw.get("shipping_included_amount", Decimal("0.0")),
    }
