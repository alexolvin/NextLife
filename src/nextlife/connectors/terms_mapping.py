"""Mapping of delivery and payment terms to specific marketplace fields (TZ v2.0 Section III.2).

DoD:
- Maps delivery options and payment options to marketplace-specific fields.
- Validates against marketplace capabilities and mapping tables.
- Any unsupported option strictly raises CapabilityNotSupported rather than being silently ignored.
- Module length <= 400 lines.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.connectors.base import CapabilityNotSupported
from nextlife.core.config import MarketplaceConfig, load_config
from nextlife.storage.models import DeliveryOption, Item, Lot


@dataclass
class MappedMarketplaceTerms:
    marketplace_key: str
    marketplace_title: str
    shipping_policy: str
    mapped_delivery: list[dict[str, Any]]
    mapped_payments: list[dict[str, Any]]
    form_payload: dict[str, Any] = field(default_factory=dict)
    human_summary_cz: str = ""


def map_terms_to_marketplace(
    marketplace_key: str,
    delivery_options: Sequence[str | dict[str, Any]],
    payment_options: Sequence[str | dict[str, Any]],
    shipping_policy: str = "BUYER_PAYS",
    marketplace_config: MarketplaceConfig | None = None,
) -> MappedMarketplaceTerms:
    """Map generic delivery and payment terms to marketplace-specific fields.

    Raises:
        CapabilityNotSupported: if any requested delivery or payment option
        is not supported by the marketplace capabilities.
    """
    key = marketplace_key.lower().strip()
    if marketplace_config is None:
        cfg = load_config()
        marketplace_config = cfg.marketplaces.get(key)
        if not marketplace_config:
            raise ValueError(f"Marketplace configuration for '{key}' not found.")

    caps = marketplace_config.capabilities or {}
    mapping = marketplace_config.mapping or {}

    # 1. Determine supported delivery options
    caps_delivery = caps.get("delivery_options")
    delivery_map = mapping.get("delivery_map", {})
    if caps_delivery is not None:
        supported_delivery = set(caps_delivery)
    else:
        supported_delivery = set(delivery_map.keys())

    # 2. Determine supported payment options
    caps_payment = caps.get("payment_options")
    payment_map = mapping.get("payment_map", {})
    if caps_payment is not None:
        supported_payment = set(caps_payment)
    else:
        supported_payment = set(payment_map.keys())

    # 3. Validate and map delivery options
    mapped_delivery_list = []
    for opt in delivery_options:
        if isinstance(opt, str):
            carrier_key = opt.lower().strip()
            price = None
        else:
            carrier_key = opt.get("carrier_key", "").lower().strip()
            price = opt.get("price")

        # Normalize special aliases
        if carrier_key in ("personal", "handover"):
            carrier_key = "pickup"

        if carrier_key not in supported_delivery:
            raise CapabilityNotSupported(
                f"Marketplace '{marketplace_config.title}' ({key}) does not support delivery option '{carrier_key}'. "
                f"Supported options: {sorted(supported_delivery)}. Option cannot be published silently."
            )

        field_name = delivery_map.get(carrier_key, carrier_key.capitalize())
        mapped_delivery_list.append(
            {
                "carrier_key": carrier_key,
                "marketplace_field": field_name,
                "price": float(price) if price is not None else 0.0,
            }
        )

    # 4. Validate and map payment options
    mapped_payment_list = []
    for opt in payment_options:
        pay_key = opt.lower().strip() if isinstance(opt, str) else opt.get("payment_key", "").lower().strip()

        # Normalize aliases
        if pay_key in ("transfer", "wire"):
            pay_key = "bank_transfer"

        if pay_key not in supported_payment:
            raise CapabilityNotSupported(
                f"Marketplace '{marketplace_config.title}' ({key}) does not support payment option '{pay_key}'. "
                f"Supported options: {sorted(supported_payment)}. Option cannot be published silently."
            )

        field_name = payment_map.get(pay_key, pay_key.capitalize())
        mapped_payment_list.append(
            {
                "payment_key": pay_key,
                "marketplace_field": field_name,
            }
        )

    # 5. Build form payload and human-readable summary
    form_delivery_values = [d["marketplace_field"] for d in mapped_delivery_list]
    form_payment_values = [p["marketplace_field"] for p in mapped_payment_list]

    form_payload = {
        "marketplace_key": key,
        "shipping_policy": shipping_policy,
        "delivery_methods": form_delivery_values,
        "payment_methods": form_payment_values,
        "delivery_details": mapped_delivery_list,
    }

    summary_parts = []
    if form_delivery_values:
        summary_parts.append(f"Doprava: {', '.join(form_delivery_values)}")
    if form_payment_values:
        summary_parts.append(f"Platba: {', '.join(form_payment_values)}")
    human_summary = " | ".join(summary_parts)

    return MappedMarketplaceTerms(
        marketplace_key=key,
        marketplace_title=marketplace_config.title,
        shipping_policy=shipping_policy,
        mapped_delivery=mapped_delivery_list,
        mapped_payments=mapped_payment_list,
        form_payload=form_payload,
        human_summary_cz=human_summary,
    )


def map_lot_terms_to_marketplace(
    session: Session,
    lot_code: str,
    marketplace_key: str,
    payment_options: list[str] | None = None,
) -> MappedMarketplaceTerms:
    """Load lot delivery options from DB and map them to specified marketplace."""
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ValueError(f"Lot with code {lot_code} not found")

    item = session.get(Item, lot.item_id)
    if not item:
        raise ValueError(f"Item {lot.item_id} not found")

    # Load delivery options
    db_opts = session.scalars(
        select(DeliveryOption).where(DeliveryOption.item_code == item.code)
    ).all()

    cfg = load_config()
    mp_cfg = cfg.marketplaces.get(marketplace_key.lower().strip())
    target_curr = getattr(mp_cfg, "currency", "CZK") if mp_cfg else "CZK"
    item_curr = item.target_currency or "CZK"

    from datetime import date
    from decimal import Decimal
    from nextlife.money.money import convert_amount
    today = date.today()

    delivery_options = []
    for opt in db_opts:
        opt_price = opt.price
        if target_curr != item_curr and opt_price:
            try:
                opt_price = convert_amount(session, Decimal(str(opt_price)), item_curr, target_curr, today)
            except Exception:
                pass
        delivery_options.append({"carrier_key": opt.carrier_key, "price": float(opt_price)})
    if not delivery_options:
        # Default fallback to pickup
        delivery_options = [{"carrier_key": "pickup", "price": 0.0}]

    if payment_options is None:
        payment_options = ["cash", "bank_transfer"]

    return map_terms_to_marketplace(
        marketplace_key=marketplace_key,
        delivery_options=delivery_options,
        payment_options=payment_options,
        shipping_policy=lot.shipping_policy,
    )
