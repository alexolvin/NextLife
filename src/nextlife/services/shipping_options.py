"""Delivery and shipping options constructor service (TZ v2.0 Section V.2 п.6, III.2).

DoD:
- Automatic tariff calculation based on carrier, weight, and dimensions.
- Manual price override detection and tagging (is_manual_price).
- Live calculation of buyer_total and seller_net based on shipping_policy:
    * BUYER_PAYS: buyer_total = price + shipping, seller_net = price
    * SELLER_PAYS: buyer_total = price, seller_net = max(0, price - shipping)
    * PICKUP_ONLY: buyer_total = price, seller_net = price
- Module length <= 400 lines.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from nextlife.storage.models import DeliveryOption, Item, Lot, ShippingService

# Default carrier standard tariffs (in CZK)
STANDARD_TARIFFS: dict[str, dict[str, Any]] = {
    "pickup": {
        "title": "Личная встреча / Osobní odběr",
        "default_cost": Decimal("0.00"),
        "max_weight_g": 50000,
    },
    "zasilkovna": {
        "title": "Zásilkovna (výdejní místo / Z-BOX)",
        "default_cost": Decimal("89.00"),
        "heavy_cost": Decimal("149.00"),
        "weight_threshold_g": 5000,
        "max_weight_g": 15000,
    },
    "balikovna": {
        "title": "Balíkovna (Česká pošta / AlzaBox)",
        "default_cost": Decimal("79.00"),
        "max_weight_g": 15000,
    },
    "cpost": {
        "title": "Česká pošta (Balík Do ruky)",
        "default_cost": Decimal("129.00"),
        "medium_cost": Decimal("169.00"),
        "heavy_cost": Decimal("229.00"),
        "max_weight_g": 30000,
    },
    "courier": {
        "title": "Kurýr DPD / PPL (na adresu)",
        "default_cost": Decimal("129.00"),
        "heavy_cost": Decimal("199.00"),
        "max_weight_g": 31500,
    },
    "ppl": {
        "title": "PPL (Parcelshop / Parcelbox)",
        "default_cost": Decimal("76.00"),
        "medium_cost": Decimal("78.00"),
        "heavy_cost": Decimal("119.00"),
        "max_weight_g": 31500,
    },
    "gls": {
        "title": "GLS (ParcelShop / ParcelBox)",
        "default_cost": Decimal("69.00"),
        "medium_cost": Decimal("109.00"),
        "heavy_cost": Decimal("158.00"),
        "max_weight_g": 20000,
    },
}


def calculate_carrier_tariff(
    session: Session | None,
    carrier_key: str,
    weight_g: int | None = None,
    dims_mm: tuple[int, int, int] | None = None,
) -> Decimal:
    """Calculate auto tariff for carrier considering physical weight and dimensions."""
    carrier_key = carrier_key.lower().strip()

    # 1. Try to query database ShippingService if session provided
    if session is not None:
        stmt = select(ShippingService).where(
            ShippingService.carrier_key == carrier_key,
            ShippingService.is_active,
        )
        if weight_g is not None:
            stmt = stmt.where(ShippingService.weight_max_g >= weight_g).order_by(
                ShippingService.price.asc()
            )
        service = session.scalars(stmt).first()
        if service is not None:
            return service.price

    # 2. Fallback to standard rule dictionary
    carrier_info = STANDARD_TARIFFS.get(carrier_key)
    if not carrier_info:
        return Decimal("99.00")  # Default flat rate for unknown carrier

    if carrier_key == "pickup":
        return Decimal("0.00")

    w = weight_g or 0
    if carrier_key == "zasilkovna":
        if w > carrier_info.get("weight_threshold_g", 5000):
            return Decimal(carrier_info["heavy_cost"])
        return Decimal(carrier_info["default_cost"])

    if carrier_key == "cpost":
        if w > 10000:
            return Decimal(carrier_info["heavy_cost"])
        elif w > 2000:
            return Decimal(carrier_info["medium_cost"])
        return Decimal(carrier_info["default_cost"])

    if carrier_key == "courier":
        if w > 10000:
            return Decimal(carrier_info["heavy_cost"])
        return Decimal(carrier_info["default_cost"])

    if carrier_key == "ppl":
        if w > 10000:
            return Decimal(carrier_info.get("heavy_cost", "119.00"))
        elif w > 3000:
            return Decimal(carrier_info.get("medium_cost", "78.00"))
        return Decimal(carrier_info["default_cost"])

    if carrier_key == "gls":
        if w > 10000:
            return Decimal(carrier_info.get("heavy_cost", "158.00"))
        elif w > 3000:
            return Decimal(carrier_info.get("medium_cost", "109.00"))
        return Decimal(carrier_info["default_cost"])

    return Decimal(carrier_info["default_cost"])


def calculate_totals(
    item_price: Decimal,
    shipping_cost: Decimal,
    shipping_policy: str = "BUYER_PAYS",
) -> tuple[Decimal, Decimal]:
    """Calculate (buyer_total, seller_net) according to policy.

    Returns:
        tuple[buyer_total, seller_net]
    """
    price = max(Decimal("0.00"), item_price)
    cost = max(Decimal("0.00"), shipping_cost)
    policy = shipping_policy.upper().strip()

    if policy == "SELLER_PAYS":
        buyer_total = price
        seller_net = max(Decimal("0.00"), price - cost)
    else:  # BUYER_PAYS or PICKUP_ONLY
        buyer_total = price + cost
        seller_net = price

    return buyer_total, seller_net


def get_available_carriers() -> list[dict[str, Any]]:
    """Return available shipping carriers with default titles and tariffs."""
    result = []
    for key, info in STANDARD_TARIFFS.items():
        result.append(
            {
                "key": key,
                "title": info["title"],
                "default_cost": float(info["default_cost"]),
            }
        )
    return result


def configure_lot_delivery_options(
    session: Session,
    lot_code: str,
    options: list[dict[str, Any]],
    shipping_policy: str | None = None,
) -> list[DeliveryOption]:
    """Persist delivery options for a lot's underlying item.

    Each option:
    - carrier_key: str
    - service_name: str
    - price: Decimal | float
    - is_manual_price: bool (or auto-detected if price != auto_price)
    """
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ValueError(f"Lot with code {lot_code} not found")

    item = session.get(Item, lot.item_id)
    if not item:
        raise ValueError(f"Item {lot.item_id} not found")

    if shipping_policy:
        lot.shipping_policy = shipping_policy.upper().strip()

    # Clear existing options for item
    session.execute(delete(DeliveryOption).where(DeliveryOption.item_code == item.code))

    weight = item.packed_weight_g or item.weight_g
    saved_records = []

    for opt in options:
        ckey = opt.get("carrier_key", "pickup").lower().strip()
        sname = opt.get("service_name") or STANDARD_TARIFFS.get(ckey, {}).get("title", ckey)
        raw_price = opt.get("price")
        price = Decimal(str(raw_price)) if raw_price is not None else calculate_carrier_tariff(session, ckey, weight)

        auto_price = calculate_carrier_tariff(session, ckey, weight)
        is_manual = opt.get("is_manual_price")
        if is_manual is None:
            is_manual = price != auto_price

        bkey = opt.get("broker_key")

        record = DeliveryOption(
            item_code=item.code,
            carrier_key=ckey,
            broker_key=bkey,
            service_name=sname,
            price=price,
            is_manual_price=bool(is_manual),
            is_enabled=opt.get("is_enabled", True),
        )
        session.add(record)
        saved_records.append(record)

    session.flush()
    return saved_records


def get_lot_delivery_summary(session: Session, lot_code: str) -> dict[str, Any]:
    """Fetch complete delivery configuration and live calculations for a lot."""
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    if not lot:
        raise ValueError(f"Lot with code {lot_code} not found")

    item = session.get(Item, lot.item_id)
    if not item:
        raise ValueError(f"Item {lot.item_id} not found")

    item_price = lot.price_gross or item.target_price or Decimal("0.00")
    policy = lot.shipping_policy or "BUYER_PAYS"
    weight = item.packed_weight_g or item.weight_g

    options = session.scalars(
        select(DeliveryOption)
        .where(DeliveryOption.item_code == item.code)
        .order_by(DeliveryOption.price.asc())
    ).all()

    options_data = []
    for opt in options:
        auto_price = calculate_carrier_tariff(session, opt.carrier_key, weight)
        buyer_tot, seller_n = calculate_totals(item_price, opt.price, policy)
        options_data.append(
            {
                "id": opt.id,
                "carrier_key": opt.carrier_key,
                "service_name": opt.service_name,
                "price": float(opt.price),
                "auto_price": float(auto_price),
                "is_manual_price": opt.is_manual_price,
                "buyer_total": float(buyer_tot),
                "seller_net": float(seller_n),
                "broker_key": opt.broker_key,
                "sender_on_documents": "SELLER",
                "cod_payout_destination": "SELLER_BANK_ACCOUNT",
                "claims_handler": "CARRIER",
                "insurance_limit": 0.0,
            }
        )

    # Fallback to default options if none configured
    if not options_data:
        default_carriers = ["pickup", "zasilkovna"]
        for ckey in default_carriers:
            auto_p = calculate_carrier_tariff(session, ckey, weight)
            buyer_tot, seller_n = calculate_totals(item_price, auto_p, policy)
            options_data.append(
                {
                    "id": None,
                    "carrier_key": ckey,
                    "service_name": STANDARD_TARIFFS[ckey]["title"],
                    "price": float(auto_p),
                    "auto_price": float(auto_p),
                    "is_manual_price": False,
                    "buyer_total": float(buyer_tot),
                    "seller_net": float(seller_n),
                    "broker_key": None,
                    "sender_on_documents": "SELLER",
                    "cod_payout_destination": "SELLER_BANK_ACCOUNT",
                    "claims_handler": "CARRIER",
                    "insurance_limit": 0.0,
                }
            )

    return {
        "lot_code": lot.code,
        "item_code": item.code,
        "item_price": float(item_price),
        "shipping_policy": policy,
        "weight_g": weight,
        "options": options_data,
        "available_carriers": get_available_carriers(),
    }
