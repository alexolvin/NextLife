"""Relevance validation and filtering for market sample listings.

Rule K-1: Module must strictly be <= 400 lines.
Ensures that only genuine, relevant market offers are used to calculate the average
or median price of an item or component, filtering out buying ads, accessories,
whole PC assemblies, non-matching models, defective goods, and fake prices.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

# Demand / Buying / Exchange regex patterns
_BUYING_DEMAND_PATTERN = re.compile(
    r"\b("
    r"koupím|koupim|kúpim|kupim|sháním|shanim|zháňam|zhanam|"
    r"poptávka|poptavka|hledám|hledam|odkoupím|odkoupim|sháníme|"
    r"vyměním|vymenim|vymena|výmena|exchange|suche|tausche|"
    r"nabídněte|nabidnete|cena dohodou|dohodou"
    r")\b",
    re.IGNORECASE,
)

# Defective / Broken / For parts regex patterns
_DEFECTIVE_PATTERN = re.compile(
    r"\b("
    r"na díly|na dily|na součástky|na soucastky|"
    r"vadná|vadna|vadný|vadny|vadne|vadné|"
    r"nefunkční|nefunkcni|nefunkčné|nefunkcne|"
    r"rozbitý|rozbity|rozbité|poškozený|poskozeny|"
    r"artefakty|defekt|chyba|defektní|crashuje|blbne|k\.?o\.?"
    r")\b",
    re.IGNORECASE,
)

# Accessory / Cooling / Packaging regex patterns (when main item is sought)
_ACCESSORY_BOX_PATTERN = re.compile(
    r"\b("
    r"pouze krabice|jen krabice|pouze box|jen box|prázdná krabice|prazdna krabice|"
    r"empty box|box only|krabička|krabicka|"
    r"vodní blok|vodni blok|waterblock|blok na|"
    r"chladič|chladic|větrák|vetrak|ventilátor|ventilator|"
    r"napájecí kabel|kabel|redukce|backplate|bracket|držák|drzak|pouzdro|obal"
    r")\b",
    re.IGNORECASE,
)

# Whole PC build / Bundle / Laptop tokens (when standalone component is sought)
_PC_BUILD_PATTERN = re.compile(
    r"\b("
    r"(?:herní|herni|gaming|výkonný|vykonny|tichý|tichy|kancelářský|kancelarsky|multimediální|custom|vodní|vodni)?\s*(?:pc|počítač|pocitac|sestava|setup|rig|workstation)|"
    r"základ pro pc|zaklad pro pc|celé pc|cele pc|stolní pc|stolni pc|komplet pc|"
    r"notebook|ntb|laptop|macbook|thinkpad|imac|omen|legion"
    r")\b",
    re.IGNORECASE,
)

_FAKE_PRICES = {Decimal("0"), Decimal("1"), Decimal("2"), Decimal("10"), Decimal("111"), Decimal("123"), Decimal("1234"), Decimal("12345"), Decimal("999999")}


def extract_core_model_tokens(query: str) -> list[str]:
    """Extract defining model tokens that must appear in matching listings."""
    q = query.lower()
    tokens: list[str] = []

    # Specific GPU series (e.g., "3090", "4080", "7900 xtx")
    m_gpu = re.findall(r"\b(rtx\s*\d{4}(?:\s*ti)?|rx\s*\d{4}(?:\s*(?:xtx|xt|gre))?|gtx\s*\d{3,4}(?:\s*ti)?)\b", q)
    for g in m_gpu:
        num = re.search(r"\d{3,4}", g)
        if num:
            tokens.append(num.group())

    # CPU models (e.g. "11900kf", "13700k", "5800x3d", "7800x")
    m_cpu = re.findall(r"\b(\d{4,5}[a-z]{0,3})\b", q)
    for c in m_cpu:
        if len(c) >= 4 and any(ch.isdigit() for ch in c):
            tokens.append(c)

    # RAM tokens (e.g. "ddr4", "ddr5", "32gb", "64gb", "16gb")
    if "ddr4" in q:
        tokens.append("ddr4")
    elif "ddr5" in q:
        tokens.append("ddr5")
    m_ram = re.findall(r"\b(\d+)\s*gb\b", q)
    for r in m_ram:
        tokens.append(r)

    # Motherboard chipsets (e.g. "x570", "b550", "z790", "b650")
    m_mb = re.findall(r"\b([abxz]\d{3,4}[a-z]?)\b", q)
    for mb in m_mb:
        tokens.append(mb)

    # General fallback: if no technical patterns matched, take distinctive words (length >= 4)
    if not tokens:
        words = [w for w in re.split(r"[\s\-_/,+.]+", q) if len(w) >= 3 and not w.isdigit()]
        tokens.extend(words[:3])

    return list(dict.fromkeys(tokens))


def is_component_query(query: str) -> bool:
    """Check if query is for an individual component rather than a full PC or bundle."""
    q = query.lower()
    has_comp_kw = any(k in q for k in (
        "rtx", "gtx", "radeon", "geforce", "grafick", "gpu",
        "intel", "core", "ryzen", "procesor", "cpu", "i9", "i7", "i5", "i3",
        "ddr4", "ddr5", "ram", "operační paměť", "operacni pamet",
        "nvme", "ssd", "disk", "chladič", "chladic", "deska", "motherboard",
        "nzxt", "kraken", "aio", "vodní", "vodni", "cooling", "ventilátor", "fan",
    )) or bool(re.search(r"\b\d{4,5}[a-z]{1,3}\b", q))
    has_pc_kw = any(k in q for k in ("herní pc", "herni pc", "gaming pc", "pc sestava", "počítač", "pocitac", "sestava"))
    return has_comp_kw and not has_pc_kw


def is_relevant_sample(
    query: str,
    title: str,
    price: Decimal | float | int | None,
    description: str = "",
) -> tuple[bool, str]:
    """Check if a sample listing is a genuinely relevant market comparable.

    Returns (is_valid, reason).
    """
    t = title.strip().lower()
    desc = description.strip().lower()
    full_text = f"{t} {desc}"

    # 1. Price validity
    if price is None:
        return False, "missing_price"
    p_dec = Decimal(str(price))
    if p_dec <= Decimal("50") or p_dec in _FAKE_PRICES:
        return False, "invalid_or_fake_price"

    # 2. Buying / demand / trade check
    if _BUYING_DEMAND_PATTERN.search(t):
        return False, "buying_demand_or_trade"

    # 3. Defective / broken check
    if _DEFECTIVE_PATTERN.search(t):
        return False, "defective_or_for_parts"

    # 4. Packaging / accessories check (when query is for a main device/card/CPU)
    q_lower = query.lower()
    query_is_acc = any(k in q_lower for k in ("krabic", "box", "blok", "chlad", "kabel", "větrák", "fan"))
    if not query_is_acc and _ACCESSORY_BOX_PATTERN.search(t):
        return False, "accessory_or_box_only"

    # 5. Component vs PC build check
    if is_component_query(query):
        is_gpu_query = bool(re.search(r"\b(rtx|gtx|rx\s*\d|radeon|geforce)\b", q_lower))
        if is_gpu_query and re.search(r"\b(herní\s*pc|herni\s*pc|gaming\s*pc|pc\s*sestava|stolní\s*pc|stolni\s*pc|celé\s*pc|cele\s*pc|počítač|pocitac|sestava|desktop|skříň|skrin)\b", t):
            return False, "gpu_query_matched_entire_pc"

        if _PC_BUILD_PATTERN.search(t):
            cleaned_t = re.sub(r"\b(?:do|pro|z)\s+(?:pc|počítače|pocitace|ntb|notebooku)\b", "", t, flags=re.IGNORECASE)
            m = _PC_BUILD_PATTERN.search(cleaned_t)
            if m:
                matched_words = [w for w in re.split(r"\s+", m.group(0).lower()) if len(w) >= 3]
                if not any(w in q_lower for w in matched_words):
                    return False, "complete_pc_build"

    # 6. Core model token check
    core_tokens = extract_core_model_tokens(query)
    if core_tokens:
        matched_any = False
        for tok in core_tokens:
            # Check if tok is present as word boundary in title or text
            pat = r"\b" + re.escape(tok) + r"\b"
            if re.search(pat, t) or re.search(pat, full_text):
                matched_any = True
                break
        if not matched_any:
            return False, "missing_core_token"

    return True, "ok"


def filter_relevant_samples(
    query: str,
    items: list[dict[str, Any]],
    expected_price_range: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Filter candidate sample items to keep strictly relevant offers."""
    valid: list[dict[str, Any]] = []

    for it in items:
        title = it.get("title") or ""
        price = it.get("price")
        desc = it.get("description") or it.get("condition_text") or ""
        ok, reason = is_relevant_sample(query, title, price, desc)
        if ok:
            valid.append(it)

    if not valid:
        return []

    # If expected price range is provided (min, max), reject extreme outliers
    if expected_price_range:
        exp_min, exp_max = expected_price_range
        valid = [
            it for it in valid
            if exp_min <= float(it.get("price") or 0) <= exp_max
        ]

    # Statistical outlier filtering using IQR if sample size >= 4
    prices = [float(it.get("price") or 0) for it in valid if it.get("price")]
    if len(prices) >= 4:
        sorted_p = sorted(prices)
        n = len(sorted_p)
        q1 = sorted_p[int(n * 0.25)]
        q3 = sorted_p[int(n * 0.75)]
        iqr = q3 - q1
        low_bound = max(50.0, q1 - 1.5 * iqr)
        high_bound = q3 + 1.5 * iqr
        valid = [it for it in valid if low_bound <= float(it.get("price") or 0) <= high_bound]

    return valid


def calculate_sample_median(
    query: str,
    items: list[dict[str, Any]],
    fallback_median: int,
    expected_price_range: tuple[int, int] | None = None,
) -> tuple[int, int, list[str]]:
    """Calculate verified median price from relevant samples.

    Returns (median_price, verified_count, sample_urls).
    For GPUs, trims overpriced listings to guarantee realistic realizable valuation.
    """
    valid = filter_relevant_samples(query, items, expected_price_range)
    if not valid:
        return fallback_median, 0, []

    prices = sorted([int(float(it.get("price") or 0)) for it in valid])
    is_gpu = bool(re.search(r"\b(rtx|gtx|rx\s*\d|radeon|geforce)\b", query.lower()))

    # For GPUs: cut off overpriced sellers asking above liquid market rates
    if is_gpu and len(prices) >= 3:
        # Take 40th percentile to represent conservative, realizable liquidation price
        p_idx = int(len(prices) * 0.4)
        med = prices[p_idx]
    else:
        mid = len(prices) // 2
        if len(prices) % 2 == 1:
            med = prices[mid]
        else:
            med = int((prices[mid - 1] + prices[mid]) / 2)

    sample_urls = [it.get("url") or it.get("external_url") or "" for it in valid if it.get("url") or it.get("external_url")]
    return med, len(prices), sample_urls[:5]
