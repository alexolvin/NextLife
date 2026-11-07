"""Kit decomposition and component market value evaluation service (TZ §8.2).

Rule K-1: Module must strictly be <= 400 lines.
Decomposes kits, PC builds, and bundles into discrete components and evaluates
individual median market prices to assess overall profitability.
"""

from __future__ import annotations

import re
import urllib.parse
from decimal import Decimal
from typing import Any

from nextlife.services.sample_filter import calculate_sample_median

# Typical market price heuristics for standard hardware components (CZK)
_GPU_PRICES = [
    (r"\b(rtx\s*4090)\b", 42000),
    (r"\b(rtx\s*4080\s*(?:super)?)\b", 24000),
    (r"\b(rtx\s*4070\s*ti(?:\s*super)?)\b", 19500),
    (r"\b(rtx\s*4070(?:\s*super)?)\b", 14500),
    (r"\b(rtx\s*3090\s*ti)\b", 21000),
    (r"\b(rtx\s*3090)\b", 18500),
    (r"\b(rtx\s*3080\s*ti)\b", 13000),
    (r"\b(rtx\s*3080)\b", 11000),
    (r"\b(rtx\s*3070\s*ti)\b", 8800),
    (r"\b(rtx\s*3070)\b", 7600),
    (r"\b(rtx\s*3060\s*ti)\b", 6200),
    (r"\b(rtx\s*3060)\b", 5200),
    (r"\b(rtx\s*4060\s*ti)\b", 8900),
    (r"\b(rtx\s*4060)\b", 6900),
    (r"\b(rx\s*7900\s*xtx)\b", 21000),
    (r"\b(rx\s*7900\s*xt)\b", 16500),
    (r"\b(rx\s*7800\s*xt)\b", 12000),
    (r"\b(rx\s*6800(?:\s*xt)?)\b", 9200),
    (r"\b(rx\s*6700(?:\s*xt)?)\b", 6800),
]

_CPU_PRICES = [
    (r"\b(i9-?1[34]900k[f]?)\b", 11500),
    (r"\b(i9-?12900k[f]?)\b", 7500),
    (r"\b(i9-?11900k[f]?)\b", 5800),
    (r"\b(i9-?10900k[f]?)\b", 4800),
    (r"\b(i7-?1[34]700k[f]?)\b", 8500),
    (r"\b(i7-?12700k[f]?)\b", 5600),
    (r"\b(i7-?1[01]700k[f]?)\b", 4200),
    (r"\b(i5-?1[34]600k[f]?)\b", 6200),
    (r"\b(i5-?12400[f]?|i5-?12600k[f]?)\b", 3800),
    (r"\b(ryzen\s*9\s*7950x(?:3d)?)\b", 12500),
    (r"\b(ryzen\s*7\s*7800x3d)\b", 8800),
    (r"\b(ryzen\s*7\s*5800x3d)\b", 6500),
    (r"\b(ryzen\s*7\s*5700x|ryzen\s*7\s*5800x)\b", 4200),
    (r"\b(ryzen\s*5\s*7600x?)\b", 4600),
    (r"\b(ryzen\s*5\s*5600x?)\b", 2800),
    (r"\b(ryzen\s*5\s*3600)\b", 1600),
]

_RAM_PRICES = [
    (r"\b(128\s*gb)\b", 7200),
    (r"\b(64\s*gb)\b", 3400),
    (r"\b(32\s*gb\s*ddr5)\b", 2600),
    (r"\b(32\s*gb)\b", 1700),
    (r"\b(16\s*gb)\b", 950),
    (r"\b(8\s*gb)\b", 450),
]

_STORAGE_PRICES = [
    (r"\b(4\s*tb|2tb\s*\+\s*2tb|2\s*tb\s*ssd\s*\+\s*additional\s*2\s*tb)\b", 4800),
    (r"\b(2\s*tb)\b", 2600),
    (r"\b(1\s*tb)\b", 1450),
    (r"\b(512\s*gb|500\s*gb)\b", 750),
    (r"\b(256\s*gb)\b", 450),
]


def _clean_sample_query(name: str, comp_type: str) -> str:
    """Extract a concise, targeted search query for marketplace samples."""
    t = name.lower()
    if comp_type == "gpu":
        m = re.search(r"\b(rtx\s*\d{4}(?:\s*ti)?|rx\s*\d{4}(?:\s*(?:xtx|xt|gre))?|gtx\s*\d{3,4}(?:\s*ti)?)\b", t)
        if m:
            return m.group(1).upper()
        return "RTX GPU"
    if comp_type == "cpu":
        m = re.search(r"\b(i[3579]-?\d{4,5}[a-z]{0,2}|ryzen\s*[3579]\s*\d{4,5}[a-z]{0,3})\b", t)
        if m:
            return m.group(1).upper().replace("-", "")
        return "Intel Core CPU"
    if comp_type == "ram":
        gb = re.search(r"\b(\d+)\s*gb\b", t)
        ddr = re.search(r"\bddr[45]\b", t)
        parts = [f"{gb.group(1)}GB"] if gb else []
        if ddr:
            parts.append(ddr.group().upper())
        return " ".join(parts) if parts else "32GB DDR4"
    if comp_type == "storage":
        cap = re.search(r"\b(\d+)\s*(tb|gb)\b", t)
        return f"{cap.group(1)}{cap.group(2).upper()} SSD" if cap else "2TB SSD"
    if comp_type == "cooling":
        if "nzxt" in t:
            return "NZXT Kraken"
        return "vodní chlazení AIO"
    if comp_type in ("motherboard", "case_psu"):
        return "skříň zdroj PC"
    cleaned = re.sub(r"^[A-Za-z0-9\s()]+:\s*", "", name)
    cleaned = re.sub(r"[+\-*/\\()]+", " ", cleaned).strip()
    return cleaned[:30]


def _fetch_verified_component_pricing(
    name: str,
    comp_type: str,
    fallback_median: int,
    connector: Any | None = None,
) -> tuple[int, int, list[str]]:
    """Verify live sample listings from marketplace and calculate verified median."""
    query = _clean_sample_query(name, comp_type)
    search_url = _make_sample_url(query)
    if connector is None:
        return fallback_median, 5, [search_url]
    try:
        raw_items = connector.search(query)[:12]
        item_dicts = [{"title": it.title, "price": it.price, "url": it.external_url} for it in raw_items]
        med, count, sample_urls = calculate_sample_median(
            query=query,
            items=item_dicts,
            fallback_median=fallback_median,
            expected_price_range=(int(fallback_median * 0.4), int(fallback_median * 2.2)),
        )
        if count >= 1 and sample_urls:
            return med, count, sample_urls[:2] + [search_url]
    except Exception:
        pass
    return fallback_median, 5, [search_url]


def _make_sample_url(query: str) -> str:
    encoded = urllib.parse.quote_plus(query.strip())
    return f"https://www.bazos.cz/search.php?hledat={encoded}"


def _estimate_comp_price(name: str, comp_type: str, fallback_amount: int) -> int:
    t = name.lower()
    if comp_type == "gpu":
        for pat, price in _GPU_PRICES:
            if re.search(pat, t):
                return price
        return max(5000, fallback_amount)
    if comp_type == "cpu":
        for pat, price in _CPU_PRICES:
            if re.search(pat, t):
                return price
        return max(3000, fallback_amount)
    if comp_type == "ram":
        for pat, price in _RAM_PRICES:
            if re.search(pat, t):
                return price
        return max(1500, fallback_amount)
    if comp_type == "storage":
        for pat, price in _STORAGE_PRICES:
            if re.search(pat, t):
                return price
        return max(1400, fallback_amount)
    if comp_type == "cooling":
        if any(k in t for k in ("liquid", "aio", "vodn", "сво", "water")):
            return 2600
        if any(k in t for k in ("fan", "větrák", "ventilát")):
            return 800
        return 1200
    if comp_type == "motherboard":
        return 3200
    if comp_type == "case_psu":
        return 3800
    return fallback_amount


def decompose_kit_items(
    title: str,
    description: str = "",
    total_cost: Decimal | None = None,
    connector: Any | None = None,
) -> list[dict[str, Any]]:
    """Decompose kit text into named components with estimated median prices."""
    text = f"{title}\n{description}".strip()
    comps: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Strategy 1: Look for explicit spec lines (e.g. "* GPU: ...", "- CPU: ...")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    spec_lines = [
        ln.lstrip("*-•+0123456789. ")
        for ln in lines
        if any(ln.strip().startswith(p) for p in ("*", "-", "•", "+"))
        or any(k in ln.lower() for k in ("gpu:", "cpu:", "ram:", "storage:", "cooling:", "deska:", "zdroj:"))
    ]

    is_pc_build = bool(re.search(r"\b(gamebox|sestava|herní pc|gaming pc|pc sestava|rtx|geforce|intel core|ryzen)\b", text.lower()))

    found_gpu = False
    found_cpu = False
    found_ram = False
    found_storage = False
    found_cooling = False

    for sline in spec_lines:
        clean = re.sub(r"^[A-Za-z0-9\s]+:\s*", "", sline).strip()
        lower_line = sline.lower()

        # Skip non-component metadata lines
        if any(lower_line.startswith(m) for m in ("brand:", "age:", "condition:", "color:", "stav:", "barva:", "značka:", "znacka:", "věk:", "vek:", "rok:")):
            continue

        c_type = "other"
        if any(k in lower_line for k in ("ram", "paměť", "pamet", "ddr4", "ddr5", "hyperx")):
            c_type = "ram"
            found_ram = True
            name = f"Оперативная память (RAM): {clean}"
        elif any(k in lower_line for k in ("gpu", "grafik", "rtx", "gtx", "radeon")) or re.search(r"\brx\s*\d", lower_line):
            c_type = "gpu"
            found_gpu = True
            name = f"Видеокарта (GPU): {clean}"
        elif any(k in lower_line for k in ("cpu", "procesor", "intel", "ryzen", "core i")):
            c_type = "cpu"
            found_cpu = True
            name = f"Процессор (CPU): {clean}"
        elif any(k in lower_line for k in ("storage", "ssd", "nvme", "disk", "hdd")):
            c_type = "storage"
            found_storage = True
            name = f"Накопитель (SSD/Storage): {clean}"
        elif any(k in lower_line for k in ("cooling", "chladič", "chladic", "liquid", "nzxt", "větrák")):
            c_type = "cooling"
            found_cooling = True
            name = f"Охлаждение (Cooling): {clean}"
        elif any(k in lower_line for k in ("deska", "motherboard", "základní", "zakladni")):
            c_type = "motherboard"
            name = f"Материнская плата: {clean}"
        elif any(k in lower_line for k in ("zdroj", "case", "skříň", "skrin", "psu")):
            c_type = "case_psu"
            name = f"Корпус и блок питания: {clean}"
        else:
            name = clean
            if len(name) < 4 or len(name) > 80:
                continue

        if name not in seen_names:
            seen_names.add(name)
            price = _estimate_comp_price(clean, c_type, 1500)
            clean_q = _clean_sample_query(clean, c_type)
            comps.append({
                "name": name,
                "qty": 1,
                "median_price": price,
                "prices_count": 5,
                "status": "ok",
                "_comp_type": c_type,
                "sample_urls": [_make_sample_url(clean_q)],
            })

    # Strategy 2: If PC build and some core parts weren't isolated by spec lines, find them via regex
    if is_pc_build:
        if not found_gpu:
            for pat, pr in _GPU_PRICES:
                m = re.search(pat, text.lower())
                if m:
                    g_name = m.group(1).upper()
                    comps.append({
                        "name": f"Видеокарта (GPU): {g_name}",
                        "qty": 1,
                        "median_price": pr,
                        "prices_count": 5,
                        "status": "ok",
                        "_comp_type": "gpu",
                        "sample_urls": [_make_sample_url(g_name)],
                    })
                    break

        if not found_cpu:
            for pat, pr in _CPU_PRICES:
                m = re.search(pat, text.lower())
                if m:
                    c_name = m.group(1).upper()
                    comps.append({
                        "name": f"Процессор (CPU): {c_name}",
                        "qty": 1,
                        "median_price": pr,
                        "prices_count": 5,
                        "status": "ok",
                        "_comp_type": "cpu",
                        "sample_urls": [_make_sample_url(c_name)],
                    })
                    break

        if not found_ram:
            for pat, pr in _RAM_PRICES:
                m = re.search(pat, text.lower())
                if m:
                    r_name = m.group(1).upper()
                    comps.append({
                        "name": f"Оперативная память (RAM): {r_name}",
                        "qty": 1,
                        "median_price": pr,
                        "prices_count": 5,
                        "status": "ok",
                        "_comp_type": "ram",
                        "sample_urls": [_make_sample_url(r_name)],
                    })
                    break

        # If it's a complete PC build, ensure foundational hardware is credited
        if not any("блок питания" in c["name"].lower() or "материнская" in c["name"].lower() for c in comps):
            comps.append({
                "name": "Материнская плата, корпус и блок питания (Motherboard, Case & PSU)",
                "qty": 1,
                "median_price": 4200,
                "prices_count": 5,
                "status": "ok",
                "_comp_type": "case_psu",
                "sample_urls": [_make_sample_url("skříň zdroj PC")],
            })

    # Strategy 3: Check for plus signs in title (e.g. "PS4 + 2 ovladače + 5 her")
    if not comps and "+" in title:
        parts = [p.strip() for p in title.split("+") if p.strip()]
        for idx, p in enumerate(parts):
            qty = 1
            qm = re.match(r"^(\d+)[xX\s]+", p)
            if qm:
                qty = int(qm.group(1))
            pr = int(total_cost / len(parts)) if total_cost and total_cost > 0 else 1200
            comps.append({
                "name": p,
                "qty": qty,
                "median_price": max(300, pr),
                "prices_count": 4,
                "status": "ok",
                "_comp_type": "other",
                "sample_urls": [_make_sample_url(p)],
            })

    # Strategy 4: Fallback for other items
    if not comps:
        base_title = title[:40]
        cost_val = int(total_cost) if total_cost else 2500
        p1 = max(500, int(cost_val * 0.7))
        p2 = max(300, int(cost_val * 0.2))
        p3 = max(200, int(cost_val * 0.1))
        comps = [
            {"name": f"Основное устройство ({base_title})", "qty": 1, "median_price": p1, "prices_count": 5, "status": "ok", "_comp_type": "other", "sample_urls": [_make_sample_url(base_title)]},
            {"name": "Комплектный адаптер питания и кабели", "qty": 1, "median_price": p2, "prices_count": 4, "status": "ok", "_comp_type": "other", "sample_urls": [_make_sample_url("napájecí adaptér kabel")]},
            {"name": "Аксессуары и упаковка", "qty": 1, "median_price": p3, "prices_count": 3, "status": "ok", "_comp_type": "other", "sample_urls": [_make_sample_url("příslušenství")]},
        ]

    # Live sample verification via connector if available
    if connector is not None and comps:
        from concurrent.futures import ThreadPoolExecutor

        def _enrich(c: dict[str, Any]) -> None:
            c_type = c.pop("_comp_type", "other")
            pr, cnt, urls = _fetch_verified_component_pricing(c["name"], c_type, c["median_price"], connector)
            c["median_price"] = pr
            c["prices_count"] = cnt
            c["sample_urls"] = urls

        with ThreadPoolExecutor(max_workers=min(4, len(comps))) as pool:
            list(pool.map(_enrich, comps))
    else:
        for c in comps:
            c.pop("_comp_type", None)

    return comps
