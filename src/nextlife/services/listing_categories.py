"""Служба автоматического определения и сопоставления категорий объявлений.

Поддерживает автоопределение категории предмета (infer_item_category),
сопоставление с рубриками площадок (get_marketplace_category_choice)
и формирование структуры для выбора категории в UI (get_listing_category_options).
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import re
from typing import Any

from nextlife.core.categories import category_title, load_category_options
from nextlife.core.config import ConfigBundle, load_config
from nextlife.services.wizard_localize import resolve_category_for_marketplace

# Шаблоны ключевых слов для автоопределения канонической категории
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "gpu",
        re.compile(
            r"\b(gpu|rtx|gtx|geforce|radeon|rx\s*\d{3,4}|видеокарт\w*|grafick\w*\s*kart\w*|graphics\s*card|quadro)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "laptops",
        re.compile(
            r"\b(ноутбук\w*|laptop\w*|notebook\w*|thinkpad|macbook|ultrabook|ideapad|zenbook|legion|latitude)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "smartphones",
        re.compile(
            r"\b(смартфон\w*|телефон\w*|iphone|galaxy\s*[sa]\d*|pixel\s*\d*|xiaomi|redmi|smartphone|mobiln[ií]\s*telefon)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "photo",
        re.compile(
            r"\b(фотоаппарат\w*|объектив\w*|камер\w*|camera|lens|canon\s*eos|nikon|fujifilm|sony\s*a\d*|zrcadlovk\w*|fotoapar[aá]t)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "bicycles",
        re.compile(
            r"\b(велосипед\w*|kolo|horsk[eé]\s*kolo|gravel|mtb|bicycle|bike|bicykl)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "furniture",
        re.compile(
            r"\b(диван\w*|стол\w*|стул\w*|шкаф\w*|мебель\w*|кресл\w*|st[uů]l|\u017eidle|sk\u0159[ií][n\u0148]|pohovk\w*|k\u0159esl\w*|n[aá]bytek|ikea)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "books",
        re.compile(
            r"\b(книг\w*|роман\w*|учебник\w*|повест\w*|kniha|knihy|book|books|isbn)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "clothing",
        re.compile(
            r"\b(одежд\w*|куртк\w*|ботинк\w*|кроссовк\w*|пальто|штаны|джинсы|oble\u010den[ií]|bunda|boty|tenisky|kalhoty|jacket|shoes|coat)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "electronics",
        re.compile(
            r"\b(монитор\w*|клавиатур\w*|мыш\w*|наушник\w*|колонк\w*|телевизор\w*|дисплей|displej|monitor|sluch[aá]tka|kl[aá]vesnice|tablet)\b",
            re.IGNORECASE,
        ),
    ),
]

_BAZOS_DEFAULTS: dict[str, str] = {
    "gpu": "pc/Grafické karty",
    "laptops": "pc/Notebooky",
    "smartphones": "mobil/Ostatní značky",
    "photo": "foto/Digitální fotoaparáty",
    "furniture": "nabytek/Křesla a gauče",
    "books": "knihy/Ostatní",
    "bicycles": "sport/Horská kola",
    "clothing": "obleceni/Bundy a Kabáty",
    "electronics": "pc/Příslušenství",
    "general": "ostatni/Všechno ostatní",
}


def infer_item_category(
    title: str,
    description: str = "",
    current_category: str | None = None,
) -> str:
    """Определяет каноническую категорию предмета (gpu, laptops, ...) по названию и описанию."""
    if current_category and current_category.strip() not in ("", "general"):
        return current_category.strip()

    combined = f"{title or ''} {description or ''}"
    for cat_key, pattern in _CATEGORY_PATTERNS:
        if pattern.search(combined):
            return cat_key

    return current_category.strip() if current_category and current_category.strip() else "general"


def get_marketplace_category_choice(
    canonical_category: str,
    mp_key: str,
    text: str = "",
    config: ConfigBundle | None = None,
) -> tuple[str, str]:
    """Возвращает (category_key, category_path) для площадки."""
    cfg = config or load_config()
    mp_cfg = cfg.marketplaces.get(mp_key)
    cat_clean = (canonical_category or "general").strip().lower()

    if mp_cfg and mp_cfg.category_tree:
        cand = _BAZOS_DEFAULTS.get(cat_clean)
        if cat_clean == "smartphones":
            txt_lower = text.lower()
            if "iphone" in txt_lower or "apple" in txt_lower:
                cand = "mobil/Apple"
            elif "samsung" in txt_lower:
                cand = "mobil/Samsung"
        elif cat_clean == "photo":
            txt_lower = text.lower()
            if "объектив" in txt_lower or "lens" in txt_lower or "objektiv" in txt_lower:
                cand = "foto/Objektivy"

        if cand:
            res = resolve_category_for_marketplace(cand, mp_cfg)
            return str(res.get("canonical") or cand), str(res.get("display_path") or cand)

        res = resolve_category_for_marketplace(canonical_category, mp_cfg)
        return str(res.get("canonical") or canonical_category), str(res.get("display_path") or canonical_category)

    # Для площадок без category_tree
    rus_title = category_title(cat_clean) or cat_clean
    return cat_clean, rus_title


def get_listing_category_options(
    mp_key: str,
    config: ConfigBundle | None = None,
) -> list[dict[str, Any]]:
    """Формирует список групп категорий с опциями для HTML <select> / <optgroup>."""
    cfg = config or load_config()
    mp_cfg = cfg.marketplaces.get(mp_key)
    groups: list[dict[str, Any]] = []

    if mp_cfg and mp_cfg.category_tree:
        for node in mp_cfg.category_tree:
            top = str(node.get("top") or "").strip()
            label = str(node.get("label") or top).strip()
            subs = [str(s).strip() for s in (node.get("subs") or []) if str(s).strip()]
            if subs:
                opts = [{"key": f"{top}/{s}", "label": s} for s in subs]
            else:
                opts = [{"key": top, "label": label}]
            groups.append({"group": label, "options": opts})
    else:
        declared = load_category_options()
        opts = [{"key": k, "label": f"{title} ({k})"} for k, title in declared]
        groups.append({"group": "Категории", "options": opts})

    return groups
