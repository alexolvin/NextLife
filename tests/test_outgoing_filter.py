from decimal import Decimal
import pytest
from pydantic import BaseModel, Field

from nextlife.comms.outgoing_filter import (
    InternalFieldLeakageError,
    check_context_for_internal_fields,
    check_text_for_blocked_values,
    render_outgoing_template,
    serialize_for_outgoing,
)


def test_price_floor_in_template_raises_exception():
    """Verify that attempting to include price_floor in a template raises InternalFieldLeakageError."""
    template = "Dobrý den, cena je {{ price }}, ale minimum je {{ price_floor }} Kč."
    context = {"price": 1000}
    with pytest.raises(InternalFieldLeakageError) as exc_info:
        render_outgoing_template(template, context)
    assert "price_floor" in str(exc_info.value)


def test_price_floor_in_context_raises_exception():
    """Verify that passing price_floor in context raises InternalFieldLeakageError."""
    template = "Dobrý den, cena je {{ price }} Kč."
    context = {"price": 1000, "price_floor": 800}
    with pytest.raises(InternalFieldLeakageError) as exc_info:
        render_outgoing_template(template, context)
    assert "price_floor" in str(exc_info.value)


def test_internal_flag_object_raises_exception():
    """Verify that an object with internal=True is blocked."""
    class SecretConfig:
        internal = True
        val = "top_secret"

    context = {"secret": SecretConfig()}
    with pytest.raises(InternalFieldLeakageError):
        check_context_for_internal_fields(context)


def test_valid_template_renders_normally():
    """Verify normal outgoing template without internal fields renders cleanly."""
    template = "Dobrý den, položka {{ item_name }} je k dispozici za {{ price }} Kč."
    context = {"item_name": "RTX 3090", "price": 15000}
    res = render_outgoing_template(template, context)
    assert res == "Dobrý den, položka RTX 3090 je k dispozici za 15000 Kč."


def test_serialize_for_outgoing_dict():
    """Verify dictionary serialization strips internal fields."""
    raw = {
        "title": "ThinkPad T14",
        "price": 15000,
        "price_floor": 12000,
        "owner_phone": "+420123456789",
        "notes": "Good condition",
    }
    serialized = serialize_for_outgoing(raw)
    assert "price_floor" not in serialized
    assert "owner_phone" not in serialized
    assert serialized["title"] == "ThinkPad T14"
    assert serialized["price"] == 15000
    assert serialized["notes"] == "Good condition"


def test_serialize_for_outgoing_pydantic():
    """Verify Pydantic model serialization strips internal fields and json_schema_extra internal=True."""
    class ItemModel(BaseModel):
        title: str
        target_price: Decimal
        price_floor: Decimal
        secret_hash: str = Field(default="abc", json_schema_extra={"internal": True})

    model = ItemModel(
        title="Monitor 27",
        target_price=Decimal("4500"),
        price_floor=Decimal("3500"),
        secret_hash="secret_payload",
    )
    serialized = serialize_for_outgoing(model)
    assert "price_floor" not in serialized
    assert "secret_hash" not in serialized
    assert serialized["title"] == "Monitor 27"
    assert serialized["target_price"] == Decimal("4500")


def test_serialize_for_outgoing_nested_structures():
    """Verify serialization strips internal fields inside nested lists and objects."""
    nested = {
        "batch_id": "B-001",
        "items": [
            {"name": "Item A", "price_floor": 100, "public_val": 200},
            {"name": "Item B", "owner_address": "Private St 1", "public_val": 300},
        ],
    }
    serialized = serialize_for_outgoing(nested)
    assert serialized["items"][0] == {"name": "Item A", "public_val": 200}
    assert serialized["items"][1] == {"name": "Item B", "public_val": 300}


def test_check_text_for_blocked_values_formats():
    """Verify check_text_for_blocked_values detects various numeric format representations."""
    blocked = [12500]

    # Different formatting variants
    variants = [
        "Nabízím za 12500 Kč.",
        "Cena je 12 500 Kč při rychlém jednání.",
        "Minimum je 12,500 Kč.",
        "Částka 12500.00 Kč.",
        "Cena 12500,00 Kč.",
    ]
    for text in variants:
        with pytest.raises(InternalFieldLeakageError):
            check_text_for_blocked_values(text, blocked)

    # Safe text with different numbers should not raise
    safe_text = "Cena je 13 000 Kč nebo 2500 Kč za komponenty."
    check_text_for_blocked_values(safe_text, blocked)


def test_check_text_for_blocked_values_decimal_no_type_error():
    """Verify check_text_for_blocked_values handles Decimal inputs without TypeError."""
    blocked = [Decimal("12500.00"), Decimal("850")]
    with pytest.raises(InternalFieldLeakageError):
        check_text_for_blocked_values("Cena: 12 500 Kč", blocked)

    with pytest.raises(InternalFieldLeakageError):
        check_text_for_blocked_values("Sleva na 850 Kč", blocked)


def test_render_outgoing_template_blocked_values():
    """Verify render_outgoing_template catches blocked formatted values."""
    template = "Dobrý den, nabízím vám cenu {{ offer }} Kč."
    context = {"offer": "12 500"}
    with pytest.raises(InternalFieldLeakageError):
        render_outgoing_template(template, context, blocked_values=[12500])

