"""Draft text and description management for lots and items (C-35, C-36, C-37).

Extracts and formats operator notes, generated draft listing descriptions, and editable final text.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from typing import Any

from nextlife.money.money import format_money
from nextlife.storage.models import Item


def is_cyrillic_dominant(text: str) -> bool:
    """Эвристика: текст преимущественно кириллица (RU) для проверки языка.

    TZ-NextLife-UI-N2 §3.3 P-E03: русский текст не должен молча уходить
    на чешские площадки. Чешский — латиница с диакритикой, поэтому
    преобладание кириллицы среди букв — надёжный признак RU-текста.
    """
    if not text:
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 3:
        return False
    cyrillic = sum(1 for ch in letters if "\u0400" <= ch <= "\u04FF")
    return cyrillic / len(letters) >= 0.5


def get_item_text_blocks(item: Item) -> dict[str, str]:
    """Return dictionary of text blocks: notes, generated_desc, final_text.

    ТЗ «Склад» §1.3.1: описание — из ``item.description`` (единственный
    канонический текст). Цепочка запасных значений из ``attrs`` удалена;
    ``item.notes`` — только заметки оператора, не запасной текст описания
    (никогда не попадает в текст объявления).
    """
    # 1. Operator notes (внутреннее поле, не текст объявления).
    notes = item.notes or ""

    # 2. Каноническое описание.
    description = item.description or ""
    if not description:
        cond_str = "Použité (dobrý stav)" if item.condition_key == "used" else (item.condition_key or "Použité")
        # TZ-NextLife-UI-N2 §1.4: формат суммы — из локали площадки;
        # цены нет — текст честно об этом говорит, без выдуманной валюты.
        if item.target_price is not None:
            price_str = format_money(item.target_price, item.target_currency)
        else:
            price_str = "není uvedena"
        description = (
            f"{item.title}\n\n"
            f"Kategorie: {item.category_key}\n"
            f"Stav: {cond_str}\n"
            f"Cena: {price_str}\n"
            f"Popis: Plně funkční, pečlivě otestováno a připraveno k odeslání."
        )

    # 3. Финальный текст = каноническое описание.
    return {
        "notes": str(notes),
        "generated_desc": str(description),
        "final_text": str(description),
    }
