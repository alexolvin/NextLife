"""Wizard step 3: finalize the lot publish parameters (approval, no publish).

The lot-creation wizard's third step lets the owner review the assembled draft
(photos + text) and set the final publish parameters: price, currency, target
marketplaces, an optional publish date, and a repost flag. Approval only saves
the parameters to the Item/Lot — it never publishes.

Storage uses existing fields only (no schema change):
- price/currency -> Item.target_price / target_currency and Lot.price_gross / currency
- marketplaces / publish_at / repost -> Item.attrs["wizard_*"]
- approval marker -> Item.attrs["wizard_status"] = "params_approved"
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from nextlife.core.exceptions import InputError
from nextlife.storage.models import Item, Lot


def finalize_draft_params(
    session: Session,
    item_code: str,
    price: Decimal | None = None,
    currency: str | None = None,
    marketplaces: list[str] | None = None,
    publish_at: str | None = None,
    repost: bool = False,
) -> str:
    """Save the final publish parameters to the Item/Lot and mark the draft
    as approved. Never publishes. Returns the item_code."""
    item = session.get(Item, item_code)
    if not item:
        raise InputError(f"Item not found: {item_code}")

    if price is not None:
        item.target_price = price
    if currency:
        item.target_currency = currency

    lot = session.query(Lot).filter(Lot.item_id == item_code).first()
    if lot is not None:
        if price is not None:
            lot.price_gross = price
        if currency:
            lot.currency = currency

    attrs = dict(item.attrs or {})
    if marketplaces is not None:
        attrs["wizard_marketplaces"] = list(marketplaces)
    if publish_at:
        attrs["wizard_publish_at"] = publish_at
    attrs["wizard_repost"] = bool(repost)
    attrs["wizard_status"] = "params_approved"
    item.attrs = attrs

    session.flush()
    return item_code
