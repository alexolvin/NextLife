"""Unified lot and item creation service (TZ v2.0 Section V.2, VI.1).

DoD:
- Single source of truth for lot creation used by both CLI and Web API.
- Prevents code duplication between API routes and CLI commands (verified via jscpd/diff).
- Allocates code, populates Item and Lot, loads category questionnaire.
- Module length <= 400 lines.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.codes import (
    allocate_code,
    format_code_display,
    format_code_tag,
)
from nextlife.core.exceptions import InputError
from nextlife.core.status import ItemStatus
from nextlife.storage.models import Item, Lot, Marketplace

CATEGORIES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "categories"


def load_category_questionnaire(category_key: str) -> dict[str, Any]:
    """Load category questions and checklist if config exists."""
    cat_file = CATEGORIES_DIR / f"{category_key}.yaml"
    if cat_file.exists():
        try:
            return yaml.safe_load(cat_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def derive_lot_currency(session: Session) -> str:
    """Валюта лота = валюта целевых (активных) площадок.

    TZ-NextLife-UI-N2 §1.1: главная (отчётная) валюта не является
    источником валюты лота. Площадки обязаны работать в одной
    валюте — при расхождении или отсутствии активных площадок
    создание лота невозможно.

    TZ Marketplaces: ANDROID-площадки включены — валюта берётся из
    конфига каждой целевой площадки (включая phone-broker канал).
    """
    rows = session.execute(
        select(Marketplace).where(Marketplace.enabled.is_(True))
    ).scalars().all()
    currencies = sorted({m.currency for m in rows})
    if not currencies:
        raise InputError(
            "Нет активных площадок: валюту лота вывести невозможно."
        )
    if len(currencies) > 1:
        detail = ", ".join(f"{m.key}={m.currency}" for m in rows)
        raise InputError(
            "Целевые площадки работают в разных валютах: "
            + detail
            + ". Создание лота невозможно."
        )
    return currencies[0]


def create_item_and_lot(
    session: Session,
    title: str,
    category_key: str,
    price: Decimal | None = None,
    currency: str | None = None,
    condition_key: str = "used",
    price_floor: Decimal | None = None,
    shipping_policy: str = "BUYER_PAYS",
    notes: str | None = None,
    attrs: dict[str, Any] | None = None,
    code_release_days: int = 90,
) -> dict[str, Any]:
    """Create physical Item and its commercial Lot offering in a single transaction."""
    title_clean = str(title).strip()
    category_clean = str(category_key).strip()

    if not title_clean:
        raise InputError("Missing required title argument.")
    if not category_clean:
        raise InputError("Missing required category argument.")

    # TZ-NextLife-UI-N2 §1.1: валюта берётся из целевых площадок;
    # явная валюта из вызова принимается без оговорок.
    if currency is None:
        currency = derive_lot_currency(session)

    # 1. Allocate 3-character unique code
    code = allocate_code(session, code_release_days)

    # 2. Physical Item
    item = Item(
        code=code,
        kind="SELL",
        title=title_clean,
        category_key=category_clean,
        attrs=attrs or {},
        condition_key=condition_key,
        defects=[],
        status=ItemStatus.DRAFT.value,
        target_price=price,
        target_currency=currency,
        notes=notes,
    )
    session.add(item)
    session.flush()

    # 3. Commercial Lot
    floor_val = price_floor if price_floor is not None else (price or Decimal("0"))
    lot = Lot(
        code=code,
        item_id=item.code,
        status="DRAFT",
        price_gross=price,
        price_floor=floor_val,
        currency=currency,
        shipping_policy=shipping_policy,
        notes=notes,
    )
    session.add(lot)
    session.flush()

    # 4. Questionnaire from category
    questionnaire = load_category_questionnaire(category_clean)

    return {
        "status": "created",
        "item_code": item.code,
        "lot_code": lot.code,
        "display_code": format_code_display(lot.code),
        "tag": format_code_tag(lot.code),
        "title": item.title,
        "category": item.category_key,
        "price": str(price) if price is not None else None,
        "currency": currency,
        "shipping_policy": lot.shipping_policy,
        "questionnaire": questionnaire,
    }
