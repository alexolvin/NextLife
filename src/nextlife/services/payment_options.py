"""Payment options management and threshold rules for lots (TZ v2.0 Section III.3.1).

DoD:
- Four payment methods: Cash on pickup, Prepay bank transfer, COD (Dobírka), Marketplace internal.
- Thresholds: cod_max_amount and prepay_required_above.
- COD strictly not offered above threshold; boundary values verified.
- Zero raw HTML in python.
- Module length <= 400 lines.
"""

import uuid
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models.deals import PaymentOption
from nextlife.storage.models.lots import Lot

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "templates"
    / "comms"
    / "payment_options.jinja2"
)


class PaymentMethodKey(StrEnum):
    CASH_ON_PICKUP = "CASH_ON_PICKUP"
    BANK_TRANSFER_PREPAY = "BANK_TRANSFER_PREPAY"
    COD = "COD"
    MARKETPLACE_INTERNAL = "MARKETPLACE_INTERNAL"


class PaymentOptionError(NextLifeError):
    """Base error for payment option calculations."""


INTERNAL_PAYMENT_MARKETPLACES = {"aukro_cz", "vinted_cz"}


def evaluate_lot_payment_options(
    lot: Lot,
    *,
    delivery_methods: list[str] | None = None,
    marketplace_key: str | None = None,
    cod_supported_by_carrier: bool = True,
    cod_max_amount: Decimal | None = None,
    prepay_required_above: Decimal | None = None,
) -> list[dict[str, Any]]:
    """Determine availability of the four payment methods for a lot.

    DoD:
    - Dobírka (COD) is strictly unavailable if price_gross > cod_max_amount or price_gross > prepay_required_above.
    - Cash on pickup available for PICKUP_ONLY or when pickup/personal delivery is present.
    - Bank transfer prepay is always available.
    - Marketplace internal available for platforms supporting escrow (e.g. Aukro).
    """
    cfg = load_config()
    effective_cod_max = cod_max_amount if cod_max_amount is not None else getattr(cfg.app, "cod_max_amount", Decimal("5000.00"))
    effective_prepay_req = prepay_required_above if prepay_required_above is not None else getattr(cfg.app, "prepay_required_above", Decimal("5000.00"))

    # Effective limit for COD is the stricter of both thresholds
    cod_limit = min(effective_cod_max, effective_prepay_req)

    price = lot.price_gross or Decimal("0.00")
    shipping_policy = (lot.shipping_policy or "BUYER_PAYS").upper()
    methods = [m.lower() for m in (delivery_methods or [])]

    is_pickup_available = (
        shipping_policy == "PICKUP_ONLY"
        or any(m in methods for m in ("pickup", "personal", "osobni_odber", "osobní odběr"))
        or not methods  # Default allows pickup unless restricted
    )

    results = []

    # 1. Cash on pickup
    cash_available = is_pickup_available
    cash_reason = None if cash_available else "Личная передача недоступна для данного лота."
    results.append({
        "payment_method": PaymentMethodKey.CASH_ON_PICKUP.value,
        "title": "Наличные при личной встрече",
        "is_available": cash_available,
        "confirmed_by": "OWNER",
        "restriction_reason": cash_reason,
        "threshold_applied": None,
    })

    # 2. Bank transfer prepay (always available)
    results.append({
        "payment_method": PaymentMethodKey.BANK_TRANSFER_PREPAY.value,
        "title": "Банковский перевод заранее (IBAN / QR)",
        "is_available": True,
        "confirmed_by": "OWNER",
        "restriction_reason": None,
        "threshold_applied": None,
    })

    # 3. Dobírka (COD)
    cod_available = True
    cod_reason = None

    if shipping_policy == "PICKUP_ONLY":
        cod_available = False
        cod_reason = "Для лота с условием 'только самовывоз' наложенный платёж не применяется."
    elif not cod_supported_by_carrier:
        cod_available = False
        cod_reason = "Выбранный перевозчик не поддерживает наложенный платёж (COD)."
    elif price > cod_limit:
        cod_available = False
        cod_reason = (
            f"Сумма лота ({price} {lot.currency}) превышает установленный порог ({cod_limit} {lot.currency}). "
            f"Предоплата обязательна."
        )

    results.append({
        "payment_method": PaymentMethodKey.COD.value,
        "title": "Наложенный платёж (Dobírka)",
        "is_available": cod_available,
        "confirmed_by": "OWNER",
        "restriction_reason": cod_reason,
        "threshold_applied": cod_limit,
    })

    # 4. Marketplace internal payment
    mp_key = (marketplace_key or "").lower()
    mp_available = mp_key in INTERNAL_PAYMENT_MARKETPLACES
    mp_reason = None if mp_available else "Площадка не поддерживает внутреннюю безопасную сделку."
    results.append({
        "payment_method": PaymentMethodKey.MARKETPLACE_INTERNAL.value,
        "title": f"Безопасная сделка площадки ({mp_key.capitalize() or 'Маркетплейс'})",
        "is_available": mp_available,
        "confirmed_by": "MARKETPLACE",
        "restriction_reason": mp_reason,
        "threshold_applied": None,
    })

    return results


def save_lot_payment_options(
    session: Session,
    lot_id: str,
    options: list[dict[str, Any]],
) -> list[PaymentOption]:
    """Persist evaluated payment options into the database for a lot."""
    # Delete existing payment options for lot
    session.query(PaymentOption).filter(PaymentOption.lot_id == lot_id).delete()
    session.flush()

    entities = []
    for opt in options:
        po = PaymentOption(
            id=str(uuid.uuid4()),
            lot_id=lot_id,
            payment_method=opt["payment_method"],
            is_available=opt["is_available"],
            confirmed_by=opt["confirmed_by"],
            threshold_applied=opt.get("threshold_applied"),
            restriction_reason=opt.get("restriction_reason"),
            details={"title": opt["title"]},
        )
        session.add(po)
        entities.append(po)

    session.flush()
    return entities


def render_payment_options_summary(
    options: list[dict[str, Any]],
    currency: str = "CZK",
) -> str:
    """Format payment options into human-readable text using Jinja2 template."""
    from jinja2 import Template
    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")
    tmpl = Template(template_str)

    cod_opt = next((o for o in options if o["payment_method"] == PaymentMethodKey.COD.value), None)
    prepay_mandatory = cod_opt is not None and not cod_opt["is_available"] and cod_opt.get("threshold_applied") is not None

    return str(
        tmpl.render(
            options=options,
            prepay_mandatory=prepay_mandatory,
            threshold=cod_opt.get("threshold_applied") if cod_opt else None,
            currency=currency,
        )
    )
