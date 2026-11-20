"""Listing text builder incorporating description, usage, specs, and delivery options (ТЗ §4.3, UI-N2).

Constructs complete structured ad copy for listings:
1. Base item description
2. Possible ways of use (from item.usage if filled)
3. Specifications / characteristics (from item.specs if filled)
4. Delivery options text block (when marketplace does not have dedicated delivery form fields)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.config import ConfigBundle, MarketplaceConfig, load_config
from nextlife.money.money import convert_amount
from nextlife.services.marketplace_shipping import (
    CARRIER_MAP,
    filter_compatible_delivery_options,
)
from nextlife.storage.models import DeliveryOption, Item

# Localized section headers
USAGE_HEADERS: dict[str, str] = {
    "ru": "Возможные способы использования:",
    "cs": "Možnosti využití:",
    "sk": "Možnosti využitia:",
    "de": "Einsatzmöglichkeiten:",
    "pl": "Możliwości zastosowania:",
    "en": "Possible uses:",
}

SPECS_HEADERS: dict[str, str] = {
    "ru": "Характеристики:",
    "cs": "Parametry a specifikace:",
    "sk": "Parametre a špecifikácia:",
    "de": "Spezifikationen:",
    "pl": "Specyfikacja:",
    "en": "Specifications:",
}

DELIVERY_HEADERS: dict[str, str] = {
    "ru": "Варианты доставки:",
    "cs": "Možnosti dopravy:",
    "sk": "Možnosti dopravy:",
    "de": "Lieferung und Abholung:",
    "pl": "Opcje dostawy:",
    "en": "Delivery options:",
}

FREE_LABELS: dict[str, str] = {
    "ru": "Бесплатно",
    "cs": "Zdarma",
    "sk": "Zadarmo",
    "de": "Kostenlos",
    "pl": "Za darmo",
    "en": "Free",
}

CARRIER_NAMES: dict[str, dict[str, str]] = {
    "pickup": {
        "ru": "Личная встреча",
        "cs": "Osobní předání",
        "sk": "Osobný odber",
        "de": "Selbstabholung",
        "pl": "Odbiór osobisty",
        "en": "Personal pickup",
    },
    "zasilkovna": {
        "ru": "Zásilkovna / Packeta",
        "cs": "Zásilkovna",
        "sk": "Packeta (Zásielkovňa)",
        "de": "Packeta",
        "pl": "Paczkomat / Przesyłka",
        "en": "Packeta / Zasilkovna",
    },
    "balikovna": {
        "ru": "Balíkovna",
        "cs": "Balíkovna",
        "sk": "Balíkovna",
        "de": "Balíkovna",
        "pl": "Balíkovna",
        "en": "Balikovna",
    },
    "cpost": {
        "ru": "Почта",
        "cs": "Česká pošta",
        "sk": "Slovenská pošta",
        "de": "Postversand",
        "pl": "Poczta",
        "en": "Postal service",
    },
    "ppl": {
        "ru": "PPL",
        "cs": "PPL",
        "sk": "PPL",
        "de": "PPL / DHL",
        "pl": "PPL",
        "en": "PPL",
    },
    "gls": {
        "ru": "GLS",
        "cs": "GLS",
        "sk": "GLS",
        "de": "GLS Paket",
        "pl": "GLS",
        "en": "GLS",
    },
    "courier": {
        "ru": "Курьер на адрес",
        "cs": "Kurýr na adresu",
        "sk": "Kuriér na adresu",
        "de": "Kurierdienst",
        "pl": "Kurier",
        "en": "Courier delivery",
    },
}


def build_composite_listing_text(
    description: str | None = None,
    usage: str | None = None,
    specs: str | None = None,
    delivery_block: str | None = None,
    lang: str = "cs",
) -> str:
    """Combine base description, usage, specs, and delivery block into listing copy.

    If usage or specs are filled, they are inserted with appropriate section headers.
    If delivery_block is provided, it is appended.
    """
    lang_key = (lang or "cs").strip().lower()
    parts: list[str] = []

    desc_clean = (description or "").strip()
    if desc_clean:
        parts.append(desc_clean)

    usage_clean = (usage or "").strip()
    if usage_clean:
        header = USAGE_HEADERS.get(lang_key, USAGE_HEADERS["cs"])
        parts.append(f"{header}\n{usage_clean}")

    specs_clean = (specs or "").strip()
    if specs_clean:
        header = SPECS_HEADERS.get(lang_key, SPECS_HEADERS["cs"])
        parts.append(f"{header}\n{specs_clean}")

    delivery_clean = (delivery_block or "").strip()
    if delivery_clean:
        parts.append(delivery_clean)

    return "\n\n".join(parts)


def build_item_delivery_text(
    session: Session | None,
    item: Item,
    marketplace_key: str,
    mp_cfg: MarketplaceConfig | None = None,
    lang: str = "cs",
    config: ConfigBundle | None = None,
) -> str:
    """Format delivery options text block if the marketplace lacks dedicated delivery fields.

    Returns an empty string if the marketplace already provides separate delivery fields.
    """
    cfg = config or load_config()
    target_mp = mp_cfg or cfg.marketplaces.get(marketplace_key)
    if not target_mp:
        return ""

    # If marketplace provides dedicated delivery form fields, do not append delivery text block
    if target_mp.has_dedicated_delivery_fields:
        return ""

    lang_key = (lang or getattr(target_mp, "language", "cs") or "cs").strip().lower()
    target_currency = getattr(target_mp, "currency", "CZK").strip().upper()
    item_currency = (item.target_currency or "CZK").strip().upper()

    # Load delivery options from DB or fallback
    options: list[Any] = []
    if session is not None:
        options = list(
            session.scalars(
                select(DeliveryOption)
                .where(DeliveryOption.item_code == item.code, DeliveryOption.is_enabled)
                .order_by(DeliveryOption.price.asc())
            ).all()
        )

    if not options:
        # Default options: pickup + zasilkovna
        options = [
            {"carrier_key": "pickup", "price": Decimal("0.00"), "service_name": "Osobní předání"},
            {"carrier_key": "zasilkovna", "price": Decimal("89.00"), "service_name": "Zásilkovna"},
        ]

    # Filter options compatible with target marketplace
    compatible, _ = filter_compatible_delivery_options(options, marketplace_key)
    if not compatible:
        return ""

    lines: list[str] = []
    free_label = FREE_LABELS.get(lang_key, FREE_LABELS["cs"])

    for opt in compatible:
        if isinstance(opt, dict):
            ckey = str(opt.get("carrier_key") or "pickup").lower().strip()
            raw_price = opt.get("price")
            svc_name = opt.get("service_name") or ckey
        else:
            ckey = str(getattr(opt, "carrier_key", "pickup")).lower().strip()
            raw_price = getattr(opt, "price", Decimal("0.00"))
            svc_name = getattr(opt, "service_name", ckey)

        price = Decimal(str(raw_price or "0.00"))

        # Convert currency if required
        if session is not None and item_currency != target_currency and price > 0:
            try:
                price = convert_amount(session, price, item_currency, target_currency, date.today())
            except Exception:
                pass

        # Localized carrier title
        carrier_title_map = CARRIER_NAMES.get(ckey, {})
        name = carrier_title_map.get(lang_key) or svc_name or ckey.capitalize()

        # Format price
        if (ckey == "pickup" or "osobn" in str(svc_name).lower()) and price == 0:
            price_str = free_label
        elif target_currency == "CZK":
            price_str = f"{int(round(price))} Kč"
        elif target_currency in ("EUR", "USD", "GBP", "PLN"):
            if price == int(price):
                price_str = f"{int(price)} {target_currency}"
            else:
                price_str = f"{price:.2f} {target_currency}"
        else:
            price_str = f"{price} {target_currency}"

        lines.append(f"- {name}: {price_str}")

    if not lines:
        return ""

    header = DELIVERY_HEADERS.get(lang_key, DELIVERY_HEADERS["cs"])
    return f"{header}\n" + "\n".join(lines)
