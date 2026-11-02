"""Wizard step 3: localize the listing per marketplace (LLM + category).

Each platform frame is a localized copy of the original listing: title +
description translated to the platform's language, the platform's category
determined, the photos with a main-photo choice, and a per-frame price/currency.
The frame is generated on demand (the «Сформировать» button) and persisted when
the owner approves it (the «Утвердить» button).

Storage uses ``Item.attrs["wizard_frames"]`` (no schema change)::

    attrs["wizard_frames"][mp_key] = {
        "title", "description", "category", "price", "currency",
        "main_photo", "status"  # "localized" | "approved"
    }
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from nextlife.core.config import ConfigBundle, MarketplaceConfig, load_config
from nextlife.core.exceptions import InputError
from nextlife.storage.models import Item

log = logging.getLogger("nextlife.wizard_localize")

# Country code -> target language name (for the LLM translation prompt).
_COUNTRY_LANGUAGE = {
    "CZ": "чешский",
    "SK": "словацкий",
    "PL": "польский",
    "DE": "немецкий",
    "AT": "немецкий",
}


def _target_language(mp_cfg: MarketplaceConfig) -> str:
    return _COUNTRY_LANGUAGE.get((mp_cfg.country or "").upper(), "чешский")


# Language code -> language name (for the LLM translation prompt).
_LANGUAGE_NAMES = {
    "ru": "русский",
    "en": "английский",
    "cs": "чешский",
    "sk": "словацкий",
    "pl": "польский",
    "de": "немецкий",
}

# Country code -> language code (search-key translation target).
_COUNTRY_LANG_CODE = {
    "CZ": "cs",
    "SK": "sk",
    "PL": "pl",
    "DE": "de",
    "AT": "de",
}

# Languages offered in the wizard step-1 search dropdown (value -> label).
SEARCH_LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
    ("cs", "Čeština"),
    ("sk", "Slovenčina"),
    ("pl", "Polski"),
    ("de", "Deutsch"),
]


def country_lang_code(mp_cfg: MarketplaceConfig) -> str:
    """The marketplace's language code (search-key translation target)."""
    return _COUNTRY_LANG_CODE.get((mp_cfg.country or "").upper(), "cs")


def translate_query(query: str, source_lang: str, target_lang: str, llm: dict[str, str]) -> str:
    """Translate the search query to the target language via the LLM.

    Returns the original query unchanged when the languages match, the query
    is empty, or the LLM is unavailable (no endpoint / call fails) — the search
    key is then used directly.
    """
    if not query or not query.strip() or source_lang == target_lang:
        return query
    from nextlife.services.llm_filter import call_llm, is_llm_configured
    if not is_llm_configured(llm):
        return query
    endpoint = (llm.get("endpoint") or "").strip()

    lang_name = _LANGUAGE_NAMES.get(target_lang, target_lang)
    prompt = (
        f"Переведи поисковый запрос на {lang_name} язык. "
        "Верни только перевод (короткий поисковый ключ), без кавычек, "
        "префиксов и пояснений.\n"
        f"Запрос: {query}"
    )
    try:
        content = call_llm(
            endpoint,
            llm.get("api_key", "").strip(),
            llm.get("model", "").strip(),
            prompt,
        )
        translated = (content or "").strip().strip('"').strip()
        return translated if translated else query
    except Exception as exc:  # noqa: BLE001 — LLM failure -> use the key directly
        log.info("Query translation to %s failed: %s", target_lang, exc)
        return query


def _fmt_price(value: Decimal) -> str:
    """Plain decimal string without trailing zeros: 22230.00 -> '22230'."""
    return format(value.normalize(), "f")


def _frame_price(item: Item, currency: str, session: Session) -> tuple[str, date | None]:
    """Frame price in the target platform's currency + the FX rate date used.

    1. The item's explicit target price (converted to ``currency`` when its
       currency differs); the ECB rate date used for the conversion is
       returned so the frame can store it (ТЗ «Склад» §4.3.3).
    2. The median of the selected reference prices, each converted to
       ``currency`` via the EUR base — references span CZK/EUR/PLN, so taking
       a raw first price would mislabel it in the platform's currency.
    3. ``("", None)`` when no price can be determined (honest fallback).
    """
    from datetime import date
    from decimal import ROUND_HALF_UP, Decimal

    from nextlife.money.money import (
        FxRateNotFoundError,
        UnknownCurrencyError,
        convert_price,
        get_latest_rate_prior_or_on,
    )

    # ECB base currency: FxRate has no row for it (rate_to_base would be 1.0).
    base = "EUR"

    def to_currency(amount: Decimal, src: str, dst: str) -> tuple[Decimal, date] | None:
        src = (src or "").strip().upper()
        dst = (dst or "").strip().upper()
        if not src or not dst or src in ("UNKNOWN", "CURRENCY_UNKNOWN"):
            return None
        if src == dst:
            return amount, date.today()
        try:
            # src -> EUR (convert_price handles src == base), then EUR -> dst.
            triple = convert_price(session, amount, src, date.today(), base)
            amount_eur = triple.amount_base
            rate_date = triple.rate_date
            if dst == base:
                return amount_eur, rate_date
            rate_dst, date_dst = get_latest_rate_prior_or_on(session, dst, date.today())
            conv = (amount_eur * rate_dst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return conv, date_dst
        except (FxRateNotFoundError, UnknownCurrencyError):
            return None

    if item.target_price is not None:
        conv = to_currency(
            Decimal(str(item.target_price)), item.target_currency or currency, currency
        )
        if conv is not None:
            return _fmt_price(conv[0]), conv[1]

    prices: list[Decimal] = []
    for ref in (item.attrs or {}).get("wizard_selected", []) or []:
        raw = (ref or {}).get("price")
        if not raw:
            continue
        try:
            amt = Decimal(str(raw))
        except Exception:
            continue
        conv = to_currency(amt, (ref or {}).get("currency", ""), currency)
        if conv is not None:
            prices.append(conv[0])
    if prices:
        prices.sort()
        return _fmt_price(prices[len(prices) // 2]), None
    return "", None


def localize_listing_text(
    title: str,
    description: str,
    mp_cfg: MarketplaceConfig,
    category: str,
    llm: dict[str, str],
    reference_titles: list[str] | None = None,
    usage: str = "",
    specs: str = "",
) -> dict[str, str]:
    """Translate title + description to the platform language and determine the
    platform's category via the LLM.

    The source text is the item's ``description`` plus the ``usage`` paragraph
    and the ``specs`` block (ТЗ «Склад» §4.3.1); all of it is translated fully
    (not shortened to 2-3 sentences) and the specs are kept as a list
    (ТЗ §4.3.2). ``reference_titles`` are the product names from the selected
    reference listings; they give the LLM the actual product name so the title
    is built from it (not from a shipping/availability phrase).

    Returns ``{"title", "description", "category"}``. When the LLM is not
    configured or the call fails, returns the original text + the global
    category (no fabricated translation).
    """
    fallback = {"title": title, "description": description, "category": category}
    from nextlife.services.llm_filter import is_llm_configured
    if not is_llm_configured(llm):
        return fallback
    endpoint = (llm.get("endpoint") or "").strip()
    lang = _target_language(mp_cfg)
    ref_lines = "\n".join(f"- {t}" for t in (reference_titles or []) if t) or "(нет)"

    # Build the category tree hint from the marketplace config.
    cat_tree = mp_cfg.category_tree or []
    if cat_tree:
        blocks: list[str] = []
        for node in cat_tree:
            top = str(node.get("top") or "").strip()
            label = str(node.get("label") or top)
            subs = [str(item).strip() for item in (node.get("subs") or []) if str(item).strip()]
            if subs:
                blocks.append(f"  - {top} ({label}): {', '.join(subs)}")
            else:
                blocks.append(f"  - {top} ({label})")
        cat_instruction = (
            "КАТЕГОРИЯ: путь по дереву площадки в формате «top/sub». "
            "top — ключ раздела из списка ниже (латиницей, как написан). "
            "sub — дословно одна подкатегория этого же раздела из списка "
            "(можно без диакритики и в нижнем регистре). "
            "Не выдумывай подкатегорию и не останавливайся на одном только разделе. "
            "Пример: ноутбук → «pc/Notebooky», объектив → «foto/Objektivy», "
            "iPhone → «mobil/Apple» (рубрика бренда, не модель).\n"
            "Дерево категорий:\n"
            + "\n".join(blocks)
            + "\n"
        )
    else:
        cat_instruction = (
            "КАТЕГОРИЯ: самая конкретная категория площадки для этого товара "
            "(на языке площадки, lowercase, латиницей).\n"
        )

    prompt = (
        f"Сформируй объявление на {lang} языке для площадки «{mp_cfg.title}» "
        f"по исходному тексту и названиям товаров из эталонных объявлений.\n"
        f"ЗАГОЛОВОК: название товара + ключевые характеристики (модель, размер, "
        f"цвет) на {lang} языке, до 60 символов. Строго не включай фразы о "
        f"доставке, наличии, оплате или отправке (типа «в наличии», «быстрая "
        f"отправка», «Zásilkovna», «doprava»).\n"
        f"ОПИСАНИЕ: переведи полностью исходное описание, использование и "
        f"технические характеристики на {lang} язык (без сокращений). "
        f"Технические характеристики сохрани списком.\n"
        f"{cat_instruction}\n"
        f"Исходный заголовок: {title}\n"
        f"Исходное описание: {description}\n"
        f"Использование: {usage or '(нет)'}\n"
        f"Характеристики: {specs or '(нет)'}\n"
        f"Исходная категория: {category or '(не задана)'}\n"
        f"Названия товаров из эталонных объявлений:\n{ref_lines}\n\n"
        'Верни только JSON: {"title": "...", "description": "...", "category": "top/sub"}\n'
        "Без пояснений."
    )
    try:
        from nextlife.services.llm_filter import call_llm

        content = call_llm(
            endpoint,
            llm.get("api_key", "").strip(),
            llm.get("model", "").strip(),
            prompt,
        )
        log.info("Localize LLM raw: %s", content)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start + 1:
            obj = json.loads(content[start:end])
            if isinstance(obj, dict):
                return {
                    "title": str(obj.get("title") or title).strip(),
                    "description": str(obj.get("description") or description).strip(),
                    "category": str(obj.get("category") or category).strip(),
                }
    except Exception as exc:  # noqa: BLE001 - honest fallback, never a raw dump
        log.warning("Localize LLM call failed: %s", exc)
    return fallback


def _fold(value: str) -> str:
    """Compare category text ignoring case, diacritics and separators."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def resolve_category_for_marketplace(
    category: str,
    mp_cfg: MarketplaceConfig,
) -> dict[str, Any]:
    """Resolve category string against marketplace category_tree.

    Returns a dict with:
    - canonical: 'top/sub' (or original category if no tree or unmatched)
    - top: top section key (e.g. 'pc') or ''
    - top_label: section label (e.g. 'PC a IT') or ''
    - sub: rubric name (e.g. 'Grafické karty') or ''
    - display_path: human-readable full path (e.g. 'PC a IT / Grafické karty')
    """
    cat_tree = mp_cfg.category_tree or []
    raw = (category or "").strip()
    if not cat_tree:
        return {
            "canonical": raw,
            "top": "",
            "top_label": "",
            "sub": raw,
            "display_path": raw,
        }

    # Case 1: path format "top/sub"
    if "/" in raw:
        top_cand, sub_cand = raw.split("/", 1)
        top_cand = top_cand.strip().lower()
        sub_cand = sub_cand.strip()
        needle = _fold(sub_cand)
        for node in cat_tree:
            node_top = str(node.get("top") or "").strip().lower()
            if node_top == top_cand:
                node_label = str(node.get("label") or node_top)
                for s in node.get("subs") or []:
                    if _fold(str(s)) == needle:
                        return {
                            "canonical": f"{node_top}/{s}",
                            "top": node_top,
                            "top_label": node_label,
                            "sub": str(s),
                            "display_path": f"{node_label} / {s}",
                        }
                return {
                    "canonical": f"{node_top}/{sub_cand}",
                    "top": node_top,
                    "top_label": node_label,
                    "sub": sub_cand,
                    "display_path": f"{node_label} / {sub_cand}",
                }

    # Case 2: bare category without "/" (e.g. "Grafické karty")
    needle = _fold(raw)
    if needle:
        hits: list[tuple[str, str, str]] = []
        for node in cat_tree:
            node_top = str(node.get("top") or "").strip().lower()
            node_label = str(node.get("label") or node_top)
            for s in node.get("subs") or []:
                if _fold(str(s)) == needle:
                    hits.append((node_top, node_label, str(s)))
        if hits:
            top_key, top_lbl, rubric = hits[0]
            return {
                "canonical": f"{top_key}/{rubric}",
                "top": top_key,
                "top_label": top_lbl,
                "sub": rubric,
                "display_path": f"{top_lbl} / {rubric}",
            }

    # Fallback for empty category when tree exists: use first rubric in first section
    if not raw and cat_tree:
        first_node = cat_tree[0]
        top_key = str(first_node.get("top") or "").strip().lower()
        top_lbl = str(first_node.get("label") or top_key)
        first_sub = str(first_node.get("subs", [""])[0]) if first_node.get("subs") else ""
        return {
            "canonical": f"{top_key}/{first_sub}" if first_sub else top_key,
            "top": top_key,
            "top_label": top_lbl,
            "sub": first_sub,
            "display_path": f"{top_lbl} / {first_sub}" if first_sub else top_lbl,
        }

    return {
        "canonical": raw,
        "top": "",
        "top_label": "",
        "sub": raw,
        "display_path": raw,
    }


def localize_frame(
    session: Session,
    item_code: str,
    mp_key: str,
    photos: list[dict[str, Any]],
    config: ConfigBundle | None = None,
) -> dict[str, Any]:
    """Build the localized frame for one marketplace.

    ``photos`` is the item's logical photo entries (passed by the caller to
    avoid a circular import with the routes module). Returns the frame data
    (localized text + category, per-frame price/currency, default main photo).
    Does not persist — the caller saves via ``save_frame``.
    """
    from nextlife.services.llm_filter import get_llm_config

    cfg = config or load_config()
    item = session.get(Item, item_code)
    if not item:
        raise InputError(f"Item not found: {item_code}")
    mp_cfg = cfg.marketplaces.get(mp_key)
    if mp_cfg is None:
        raise InputError(f"Marketplace not in config: {mp_key}")

    # Source text: the item's own fields (ТЗ «Склад» §4.3.1) — description,
    # then the usage paragraph, then the specs block. item.notes is NOT passed
    # to localization (ТЗ §4.3.4).
    ru_title = item.title or item_code
    ru_text = item.description or ""
    usage = item.usage or ""
    specs = item.specs or ""

    ref_titles = [
        (r or {}).get("title", "")
        for r in (item.attrs or {}).get("wizard_selected", []) or []
        if (r or {}).get("title")
    ]
    log.info(
        "Localize source: title=%r cat=%r refs=%r desc_len=%d usage_len=%d specs_len=%d",
        ru_title, item.category_key, ref_titles, len(ru_text), len(usage), len(specs),
    )
    localized = localize_listing_text(
        ru_title, ru_text, mp_cfg, item.category_key or "", get_llm_config(),
        reference_titles=ref_titles, usage=usage, specs=specs,
    )

    main_photo = ""
    for p in photos:
        if p.get("url") and not p.get("broken"):
            main_photo = p.get("display_id") or p.get("id") or ""
            break

    price, price_rate_date = _frame_price(item, mp_cfg.currency, session)
    cat_res = resolve_category_for_marketplace(localized["category"], mp_cfg)

    return {
        "title": localized["title"],
        "description": localized["description"],
        "category": cat_res["canonical"],
        "price": price,
        "currency": mp_cfg.currency,
        "price_rate_date": str(price_rate_date) if price_rate_date else None,
        "photos": photos,
        "main_photo": main_photo,
        "status": "localized",
    }


def save_frame(
    session: Session,
    item_code: str,
    mp_key: str,
    data: dict[str, Any],
    approved: bool = False,
) -> str:
    """Save a localized/approved frame to ``attrs["wizard_frames"][mp_key]``."""
    item = session.get(Item, item_code)
    if not item:
        raise InputError(f"Item not found: {item_code}")
    frames = dict((item.attrs or {}).get("wizard_frames") or {})
    frame = dict(data)
    frame["status"] = "approved" if approved else "localized"
    frames[mp_key] = frame
    attrs = dict(item.attrs or {})
    attrs["wizard_frames"] = frames
    item.attrs = attrs
    session.flush()
    return item_code


def approved_marketplaces(item: Item) -> list[str]:
    """Marketplace keys whose frame is approved (for step 4 dry-run)."""
    frames = (item.attrs or {}).get("wizard_frames") or {}
    return [key for key, frame in frames.items() if (frame or {}).get("status") == "approved"]
