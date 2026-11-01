"""Wizard step 2: create/supplement an Item draft from selected search results.

The lot-creation wizard's second step links the selected step-1 results to an
Item draft. The selected results are stored in the Item's existing JSONB
``attrs["wizard_selected"]`` (no invented fields); notes in ``Item.notes``.
If no item_code is given, a new Item (kind=SELL, 3-char code via allocate_code)
is created via create_item_and_lot.
"""

from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.categories import declared_category_keys
from nextlife.core.exceptions import InputError
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.models import Item

# Default category for a new draft (refined later in the wizard).
_DEFAULT_CATEGORY = "electronics"


def create_draft_from_selection(
    session: Session,
    selected: list[dict[str, Any]],
    notes: str = "",
    item_code: str | None = None,
    category: str | None = None,
) -> str:
    """Create (or supplement) an Item draft from selected search results.

    If ``item_code`` is given, the existing Item is supplemented (selected
    results merged into ``attrs["wizard_selected"]``). Otherwise a new Item
    (kind=SELL, 3-char code via allocate_code) is created. ``category`` (a
    declared category key) is used for a new Item; without it the default
    category applies. Returns the item_code.
    """
    if item_code:
        item = session.get(Item, item_code)
        if not item:
            raise InputError(f"Item not found: {item_code}")
        attrs = dict(item.attrs or {})
        attrs["wizard_selected"] = selected
        item.attrs = attrs
        if notes:
            item.notes = notes
        session.flush()
        return item_code

    category_key = _DEFAULT_CATEGORY
    if category:
        category_key = category.strip()
        if category_key not in declared_category_keys():
            raise InputError(f"Unknown category: {category_key}")

    title = selected[0].get("title", "") if selected else "Новый лот"
    created = create_item_and_lot(
        session=session,
        title=title,
        category_key=category_key,
        notes=notes or None,
        attrs={"wizard_selected": selected},
    )
    return str(created["item_code"])
