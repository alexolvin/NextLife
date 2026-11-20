"""Служба автоматического определения и сопоставления категорий объявлений.

Поддерживает автоопределение категории предмета (infer_item_category),
сопоставление с рубриками площадок (get_marketplace_category_choice)
и формирование структуры для выбора категории в UI (get_listing_category_options).
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
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
    "gpu": "pc/Grafické karty", "laptops": "pc/Notebooky", "smartphones": "mobil/Ostatní značky",
    "photo": "foto/Digitální fotoaparáty", "furniture": "nabytek/Křesla a gauče", "books": "knihy/Ostatní",
    "bicycles": "sport/Horská kola", "clothing": "obleceni/Bundy a Kabáty", "electronics": "pc/Příslušenství",
    "general": "ostatni/Všechno ostatní",
}

_SBAZAR_DEFAULTS: dict[str, str] = {
    "gpu": "pocitace/Grafické karty", "laptops": "pocitace/Notebooky", "smartphones": "mobily/Chytré telefony",
    "photo": "foto/Digitální fotoaparáty", "furniture": "dum-byt-zahrada/Nábytek", "books": "knihy/Odborná literatura",
    "bicycles": "sport/Horská kola", "clothing": "obleceni/Bundy a kabáty", "electronics": "pocitace/Příslušenství",
    "general": "ostatni/Všechno ostatní",
}

_BAZOS_SK_DEFAULTS: dict[str, str] = {
    "gpu": "pc/Grafické karty", "laptops": "pc/Notebooky", "smartphones": "mobil/Apple",
    "photo": "foto/Digitálne fotoaparáty", "furniture": "nabytok/Kreslá a gauče", "books": "knihy/Beletria",
    "bicycles": "sport/Bicykle", "clothing": "oblecenie/Bundy a Kabáty", "electronics": "pc/Príslušenstvo",
    "general": "ostatne/Ostatné",
}

_EBAY_DEFAULTS: dict[str, str] = {
    "gpu": "computers/Graphics/Video Cards", "laptops": "computers/Laptops & Netbooks",
    "smartphones": "cell_phones/Cell Phones & Smartphones", "photo": "cameras_photo/Digital Cameras",
    "furniture": "collectibles/Other Collectibles", "books": "books/Fiction & Literature",
    "bicycles": "sporting_goods/Cycling", "clothing": "clothing_shoes/Men's Clothing",
    "electronics": "computers/Computer Components & Parts", "general": "collectibles/Other Collectibles",
}

_AUKRO_DEFAULTS: dict[str, str] = {
    "gpu": "pocitace-a-hry/Grafické karty", "laptops": "pocitace-a-hry/Notebooky a příslušenství",
    "smartphones": "mobily-a-gps/Mobilní telefony Apple", "photo": "foto/Digitální zrcadlovky",
    "furniture": "dum-a-zahrada/Nábytek", "books": "knihy/Odborná literatura",
    "bicycles": "sport/Horská a silniční kola", "clothing": "moda-a-sperky/Pánské oblečení",
    "electronics": "pocitace-a-hry/Příslušenství", "general": "sberatelstvi/Ostatní sběratelství",
}

_VINTED_DEFAULTS: dict[str, str] = {
    "gpu": "elektronika/Ostatní elektronika", "laptops": "elektronika/Čtečky knih a tablety",
    "smartphones": "elektronika/Příslušenství k telefonům a kryty", "photo": "elektronika/Fotoaparáty a příslušenství",
    "furniture": "domov/Dekorace a doplňky", "books": "domov/Knihy a literatura",
    "bicycles": "muzi/Pánské doplňky a tašky", "clothing": "muzi/Bundy a kabáty",
    "electronics": "elektronika/Sluchátka a přenosné audio", "general": "domov/Ostatní pro domov",
}

_PLATFORM_DEFAULTS: dict[str, dict[str, str]] = {
    "bazos_cz": _BAZOS_DEFAULTS, "sbazar_cz": _SBAZAR_DEFAULTS, "bazos_sk": _BAZOS_SK_DEFAULTS,
    "ebay": _EBAY_DEFAULTS, "aukro_cz": _AUKRO_DEFAULTS, "vinted": _VINTED_DEFAULTS, "vinted_cz": _VINTED_DEFAULTS,
    "bazar_cz": {"gpu": "pocitace/Grafické karty", "laptops": "pocitace/Notebooky", "smartphones": "mobily/Chytré telefony", "photo": "foto-kamery/Digitální fotoaparáty", "furniture": "nabytek/Nábytek", "books": "knihy/Knihy", "bicycles": "sport-outdoor/Jízdní kola", "clothing": "moda-obleceni/Pánské oblečení", "electronics": "elektro/Spotřební elektronika", "general": "ostatni/Všechno ostatní"},
    "mimibazar_cz": {"gpu": "ostatni/Všechno ostatní", "laptops": "ostatni/Všechno ostatní", "smartphones": "ostatni/Všechno ostatní", "photo": "ostatni/Všechno ostatní", "furniture": "domacnost/Nábytek a dekorace", "books": "knihy-a-zabava/Knihy a leporela", "bicycles": "sport-a-kocarky/Kola a odrážedla", "clothing": "detske-obleceni/Bundy a kabáty", "electronics": "ostatni/Všechno ostatní", "general": "ostatni/Všechno ostatní"},
    "hyperinzerce_cz": {"gpu": "pocitace/Grafické karty", "laptops": "pocitace/Notebooky", "smartphones": "mobily/Chytré telefony", "photo": "foto/Digitální fotoaparáty", "furniture": "nabytek/Sedací soupravy a křesla", "books": "knihy/Odborná literatura", "bicycles": "sport/Jízdní kola", "clothing": "obleceni/Pánské oblečení", "electronics": "elektro/Audio a video", "general": "ostatni/Vše ostatní"},
    "annonce_cz": {"gpu": "pocitace/Grafické karty", "laptops": "pocitace/Notebooky, netbooky", "smartphones": "mobily/Chytré telefony", "photo": "foto-optika/Digitální fotoaparáty", "furniture": "dum-byt-zahrada/Nábytek", "books": "knihy/Knihy a učebnice", "bicycles": "sport/Jízdní kola a cyklistika", "clothing": "obleceni/Pánské oblečení", "electronics": "elektro/Spotřební elektronika", "general": "ostatni/Všechno ostatní"},
    "trhknih_cz": {"gpu": "odborna/Počítače a IT", "laptops": "odborna/Počítače a IT", "smartphones": "odborna/Počítače a IT", "photo": "hobby/Umění a architektura", "furniture": "hobby/Zahrada a kutilství", "books": "beletrie/Romány", "bicycles": "hobby/Sport a fitness", "clothing": "hobby/Záliby a hobby", "electronics": "odborna/Počítače a IT", "general": "beletrie/Romány"},
    "facebook_marketplace": {"gpu": "elektronika/Komponenty a grafické karty", "laptops": "elektronika/Počítače a notebooky", "smartphones": "elektronika/Mobilní telefony", "photo": "elektronika/Fotoaparáty a optika", "furniture": "domacnost/Nábytek", "books": "zabava/Knihy a časopisy", "bicycles": "sport/Jízdní kola a cyklistika", "clothing": "obleceni/Pánské oblečení", "electronics": "elektronika/Televize a audio", "general": "domacnost/Bytové dekorace"},
}

def _fold(value: str) -> str:
    """Нормализует строку для сопоставления категорий без учета регистра и диакритики."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


# Шаблоны прямого сопоставления текста объявления с рубриками дерева площадок
_TREE_PATTERNS: list[tuple[re.Pattern[str], list[str]]] = [
    # Звуковые карты / аудиоинтерфейсы / предусилители
    (
        re.compile(
            r"\b(звуков\w*\s*карт\w*|sound\s*card|audio\s*interface|audio\s*rozhran[ií]|"
            r"zvukov[aá]\s*karta|zvukov[eé]\s*karty|zvukovka|preamp|p[rř]edzesilova[cč]|"
            r"m-audio|focusrite|scarlett|motu|behringer\s*u-phoria)\b",
            re.IGNORECASE,
        ),
        ["Zvukové karty a audio rozhraní", "Zvukové karty", "Zvuková technika"],
    ),
    # Микрофоны
    (
        re.compile(r"\b(микрофон\w*|mikrofon\w*|microphone|shure\s*sm|rode\s*nt)\b", re.IGNORECASE),
        ["Mikrofony"],
    ),
    # Видеокарты (GPU)
    (
        re.compile(
            r"\b(gpu|rtx|gtx|geforce|radeon|rx\s*\d{3,4}|видеокарт\w*|grafick\w*\s*kart\w*|graphics\s*card|quadro)\b",
            re.IGNORECASE,
        ),
        ["Grafické karty"],
    ),
    # Ноутбуки
    (
        re.compile(
            r"\b(ноутбук\w*|laptop\w*|notebook\w*|thinkpad|macbook|ultrabook|ideapad|zenbook|legion|latitude)\b",
            re.IGNORECASE,
        ),
        ["Notebooky"],
    ),
    # Процессоры (CPU)
    (
        re.compile(r"\b(процессор\w*|procesor\w*|cpu|intel\s*core|ryzen)\b", re.IGNORECASE),
        ["Procesory"],
    ),
    # Материнские платы
    (
        re.compile(r"\b(материнск\w*\s*плат\w*|z[aá]kladn[ií]\s*desky?|motherboard|mainboard)\b", re.IGNORECASE),
        ["Základní desky"],
    ),
    # Оперативная память (RAM)
    (
        re.compile(r"\b(оперативн\w*\s*памят\w*|ram\s*pam[eě]t\w*|ddr[345]|pam[eě]ti)\b", re.IGNORECASE),
        ["RAM paměti", "Paměti"],
    ),
    # Накопители / SSD / HDD
    (
        re.compile(r"\b(ssd|hdd|жестк\w*\s*диск\w*|pevn[eé]\s*disky|hard\s*disk|nvme)\b", re.IGNORECASE),
        ["Pevné disky a SSD", "Hard disky, SSD"],
    ),
    # Мониторы
    (
        re.compile(r"\b(монитор\w*|lcd\s*monitory?|monitory?)\b", re.IGNORECASE),
        ["Monitory", "LCD monitory"],
    ),
    # Клавиатуры и мыши
    (
        re.compile(r"\b(клавиатур\w*|kl[aá]vesnice|мыш\w*|my[sš]i)\b", re.IGNORECASE),
        ["Klávesnice a myši", "Klávesnice", "Myši"],
    ),
    # Наушники
    (
        re.compile(r"\b(наушник\w*|sluch[aá]tka|headphones|headset)\b", re.IGNORECASE),
        ["Sluchátka"],
    ),
    # Смарт-часы
    (
        re.compile(r"\b(chytr[eé]\s*hodinky|smartwatch|apple\s*watch|galaxy\s*watch)\b", re.IGNORECASE),
        ["Chytré hodinky"],
    ),
    # Смартфоны Apple
    (
        re.compile(r"\b(iphone|apple)\b", re.IGNORECASE),
        ["Apple", "Chytré telefony"],
    ),
    # Смартфоны Samsung
    (
        re.compile(r"\b(samsung|galaxy\s*[sa]\d*)\b", re.IGNORECASE),
        ["Samsung", "Chytré telefony"],
    ),
    # Смартфоны Xiaomi
    (
        re.compile(r"\b(xiaomi|redmi)\b", re.IGNORECASE),
        ["Xiaomi", "Chytré telefony"],
    ),
    # Смартфоны общие
    (
        re.compile(r"\b(смартфон\w*|телефон\w*|smartphone|mobiln[ií]\s*telefon)\b", re.IGNORECASE),
        ["Chytré telefony", "Mobilní telefony", "Ostatní značky"],
    ),
    # Фототехника - объективы
    (
        re.compile(r"\b(объектив\w*|lens|objektiv\w*)\b", re.IGNORECASE),
        ["Objektivy"],
    ),
    # Фототехника - камеры
    (
        re.compile(r"\b(фотоаппарат\w*|камер\w*|canon\s*eos|nikon|fujifilm|sony\s*a\d*|zrcadlovk\w*|fotoapar[aá]t)\b", re.IGNORECASE),
        ["Digitální fotoaparáty", "Fotoaparáty", "Zrcadlovky"],
    ),
    # Велосипеды
    (
        re.compile(r"\b(велосипед\w*|kolo|horsk[eé]\s*kolo|gravel|mtb|bicycle|bike|bicykl)\b", re.IGNORECASE),
        ["Horská kola", "Jízdní kola"],
    ),
    # Мебель
    (
        re.compile(r"\b(диван\w*|стол\w*|стул\w*|шкаф\w*|мебель\w*|кресл\w*|st[uů]l|[zž]idle|sk[rř][ií][nň]|pohovk\w*|k[rř]esl\w*|n[aá]bytek|ikea)\b", re.IGNORECASE),
        ["Nábytek", "Křesla a gauče", "Stoly a židle"],
    ),
    # Книги
    (
        re.compile(r"\b(книг\w*|роман\w*|учебник\w*|повест\w*|kniha|knihy|book|books|isbn)\b", re.IGNORECASE),
        ["Knihy", "Učebnice", "Ostatní"],
    ),
]


def _find_in_tree(target_rubrics: list[str], cat_tree: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Ищет подходящую рубрику в дереве категорий площадки по списку приоритетных названий."""
    for target in target_rubrics:
        t_fold = _fold(target)
        for node in cat_tree:
            node_top = str(node.get("top") or "").strip()
            node_label = str(node.get("label") or node_top).strip()
            for sub in node.get("subs") or []:
                s_str = str(sub).strip()
                if _fold(s_str) == t_fold:
                    canonical = f"{node_top}/{s_str}"
                    display = f"{node_label} / {s_str}"
                    return canonical, display
            subs = node.get("subs") or []
            if not subs and _fold(node_label) == t_fold:
                return node_top, node_label
    return None


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
    """Возвращает (category_key, category_path) для площадки индивидуально по тексту и дереву категорий."""
    cfg = config or load_config()
    mp_cfg = cfg.marketplaces.get(mp_key)
    cat_clean = (canonical_category or "general").strip().lower()

    if mp_cfg and mp_cfg.category_tree:
        # 1. Прямое сопоставление ключевых слов из текста с деревом категорий площадки
        if text:
            for pattern, target_rubrics in _TREE_PATTERNS:
                if pattern.search(text):
                    match = _find_in_tree(target_rubrics, mp_cfg.category_tree)
                    if match:
                        return match

        # 2. Сопоставление по канонической категории
        cand = _PLATFORM_DEFAULTS.get(mp_key, _BAZOS_DEFAULTS).get(cat_clean)
        if cand:
            res = resolve_category_for_marketplace(cand, mp_cfg)
            return str(res.get("canonical") or cand), str(res.get("display_path") or cand)

        res = resolve_category_for_marketplace(canonical_category, mp_cfg)
        return str(res.get("canonical") or canonical_category), str(res.get("display_path") or canonical_category)

    # Для площадок без category_tree
    rus_title = category_title(cat_clean) or cat_clean
    return cat_clean, rus_title


def _build_listing_category_options(
    mp_key: str,
    cfg: ConfigBundle,
) -> list[dict[str, Any]]:
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


@lru_cache(maxsize=32)
def _get_listing_category_options_cached(mp_key: str) -> list[dict[str, Any]]:
    return _build_listing_category_options(mp_key, load_config())


def get_listing_category_options(
    mp_key: str,
    config: ConfigBundle | None = None,
) -> list[dict[str, Any]]:
    """Формирует список групп категорий с опциями для HTML <select> / <optgroup>."""
    if config is not None:
        return _build_listing_category_options(mp_key, config)
    return _get_listing_category_options_cached(mp_key)
