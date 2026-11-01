from decimal import Decimal

import pytest

from nextlife.comms.outgoing_filter import InternalFieldLeakageError, render_outgoing_template
from nextlife.pricing.models import (
    InternalLotPricingSchema,
    PublicLotPricingSchema,
    ShippingPolicy,
    serialize_outgoing_pricing,
)


def test_public_pricing_schema_excludes_price_floor():
    """DoD: Public serializer schema strictly does NOT contain price_floor."""
    fields = PublicLotPricingSchema.model_fields.keys()
    assert "price_floor" not in fields
    assert "price_gross" in fields
    assert "shipping_policy" in fields


def test_serialize_outgoing_pricing_strips_price_floor():
    """Verify serialize_outgoing_pricing never includes price_floor."""
    internal = InternalLotPricingSchema(
        item_code="348",
        price_gross=Decimal("15000.00"),
        price_base=Decimal("600.00"),
        price_floor=Decimal("12000.00"),
        negotiable=True,
        shipping_policy=ShippingPolicy.BUYER_PAYS,
    )
    public_dict = serialize_outgoing_pricing(internal)
    assert "price_floor" not in public_dict
    assert public_dict["price_gross"] == Decimal("15000.00")
    assert public_dict["item_code"] == "348"


def test_outgoing_filter_blocks_price_floor_leakage():
    """Verify passing internal pricing to template renderer raises InternalFieldLeakageError."""
    internal = InternalLotPricingSchema(
        item_code="348",
        price_gross=Decimal("15000.00"),
        price_floor=Decimal("12000.00"),
        negotiable=True,
        shipping_policy=ShippingPolicy.BUYER_PAYS,
    )
    template = "Předmět stojí {{ pricing.price_gross }} Kč."
    with pytest.raises(InternalFieldLeakageError):
        # Passing object with internal=True marker
        render_outgoing_template(template, {"pricing": internal})
