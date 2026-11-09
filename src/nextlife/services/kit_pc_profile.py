"""Specialized Computer Build (PC Build) profile analyzer service (TZ §8.2).

Rule K-1: Module must strictly be <= 400 lines.
Detects computer builds and decomposes them into full component sets:
GPU, CPU, RAM (with all syntax variants like 32G, 32GB, 32g), Storage (SSD),
Motherboard, Case & PSU, and CPU Cooling.
"""

from __future__ import annotations

import re
import urllib.parse
from decimal import Decimal
from typing import Any

from nextlife.services.sample_filter import calculate_sample_median

# GPU models and typical secondary market median prices (CZK)
_GPU_CATALOG = [
    (r"\b(rtx\s*4090)\b", "NVIDIA GeForce RTX 4090 24GB", 42000, "RTX 4090"),
    (r"\b(rtx\s*4080\s*super)\b", "NVIDIA GeForce RTX 4080 Super 16GB", 25500, "RTX 4080 Super"),
    (r"\b(rtx\s*4080)\b", "NVIDIA GeForce RTX 4080 16GB", 23000, "RTX 4080"),
    (r"\b(rtx\s*4070\s*ti\s*super)\b", "NVIDIA GeForce RTX 4070 Ti Super 16GB", 20500, "RTX 4070 Ti Super"),
    (r"\b(rtx\s*4070\s*ti)\b", "NVIDIA GeForce RTX 4070 Ti 12GB", 18000, "RTX 4070 Ti"),
    (r"\b(rtx\s*4070\s*super)\b", "NVIDIA GeForce RTX 4070 Super 12GB", 15500, "RTX 4070 Super"),
    (r"\b(rtx\s*4070)\b", "NVIDIA GeForce RTX 4070 12GB", 13800, "RTX 4070"),
    (r"\b(rtx\s*4060\s*ti)\b", "NVIDIA GeForce RTX 4060 Ti", 8900, "RTX 4060 Ti"),
    (r"\b(rtx\s*4060)\b", "NVIDIA GeForce RTX 4060", 6900, "RTX 4060"),
    (r"\b(rtx\s*3090\s*ti)\b", "NVIDIA GeForce RTX 3090 Ti 24GB", 21500, "RTX 3090 Ti"),
    (r"\b(rtx\s*3090)\b", "NVIDIA GeForce RTX 3090 24GB", 19000, "RTX 3090"),
    (r"\b(rtx\s*3080\s*ti)\b", "NVIDIA GeForce RTX 3080 Ti 12GB", 13000, "RTX 3080 Ti"),
    (r"\b(rtx\s*3080)\b", "NVIDIA GeForce RTX 3080 10GB", 10500, "RTX 3080"),
    (r"\b(rtx\s*3070\s*ti)\b", "NVIDIA GeForce RTX 3070 Ti 8GB", 8500, "RTX 3070 Ti"),
    (r"\b(rtx\s*3070)\b", "NVIDIA GeForce RTX 3070 8GB", 7500, "RTX 3070"),
    (r"\b(rtx\s*3060\s*ti)\b", "NVIDIA GeForce RTX 3060 Ti 8GB", 6200, "RTX 3060 Ti"),
    (r"\b(rtx\s*3060)\b", "NVIDIA GeForce RTX 3060 12GB", 5200, "RTX 3060"),
    (r"\b(rtx\s*2080\s*(?:ti|super)?)\b", "NVIDIA GeForce RTX 2080", 5500, "RTX 2080"),
    (r"\b(rtx\s*2070\s*super?)\b", "NVIDIA GeForce RTX 2070", 4400, "RTX 2070"),
    (r"\b(rtx\s*2060\s*(?:super)?)\b", "NVIDIA GeForce RTX 2060", 3500, "RTX 2060"),
    (r"\b(gtx\s*1660\s*(?:ti|super)?)\b", "NVIDIA GeForce GTX 1660 Series", 2600, "GTX 1660"),
    (r"\b(gtx\s*1080\s*(?:ti)?)\b", "NVIDIA GeForce GTX 1080", 3200, "GTX 1080"),
    (r"\b(gtx\s*1070\s*(?:ti)?)\b", "NVIDIA GeForce GTX 1070", 2300, "GTX 1070"),
    (r"\b(rx\s*7900\s*xtx)\b", "AMD Radeon RX 7900 XTX 24GB", 21000, "RX 7900 XTX"),
    (r"\b(rx\s*7900\s*xt)\b", "AMD Radeon RX 7900 XT 20GB", 16500, "RX 7900 XT"),
    (r"\b(rx\s*7800\s*xt)\b", "AMD Radeon RX 7800 XT 16GB", 12500, "RX 7800 XT"),
    (r"\b(rx\s*7700\s*xt)\b", "AMD Radeon RX 7700 XT 12GB", 9500, "RX 7700 XT"),
    (r"\b(rx\s*7600(?:\s*xt)?)\b", "AMD Radeon RX 7600", 6200, "RX 7600"),
    (r"\b(rx\s*6950\s*xt|rx\s*6900\s*xt)\b", "AMD Radeon RX 6900 XT 16GB", 12000, "RX 6900 XT"),
    (r"\b(rx\s*6800\s*xt)\b", "AMD Radeon RX 6800 XT 16GB", 10000, "RX 6800 XT"),
    (r"\b(rx\s*6800)\b", "AMD Radeon RX 6800 16GB", 8800, "RX 6800"),
    (r"\b(rx\s*6700\s*xt|rx\s*6750\s*xt)\b", "AMD Radeon RX 6700 XT 12GB", 6800, "RX 6700 XT"),
    (r"\b(rx\s*6600(?:\s*xt)?)\b", "AMD Radeon RX 6600", 4400, "RX 6600"),
    (r"\b(rx\s*5700\s*xt)\b", "AMD Radeon RX 5700 XT", 3500, "RX 5700 XT"),
    (r"\b(rx\s*580)\b", "AMD Radeon RX 580 8GB", 1600, "RX 580"),
]

# CPU models and typical prices (CZK)
_CPU_CATALOG = [
    # Intel Core specific
    (r"\b(i9-?1[34]900k[sf]?)\b", "Intel Core i9-14900K/13900K", 11500, "Core i9 14900K"),
    (r"\b(i9-?12900k[f]?)\b", "Intel Core i9-12900K", 7500, "Core i9 12900K"),
    (r"\b(i7-?1[34]700k[f]?)\b", "Intel Core i7-14700K/13700K", 8500, "Core i7 14700K"),
    (r"\b(i7-?12700k[f]?)\b", "Intel Core i7-12700K", 5600, "Core i7 12700K"),
    (r"\b(i5-?1[34]600k[f]?)\b", "Intel Core i5-14600K/13600K", 6200, "Core i5 13600K"),
    (r"\b(i5-?12400[f]?|i5-?12600k[f]?|i5-?13400[f]?|i5-?14400[f]?)\b", "Intel Core i5 (12/13/14-gen)", 3800, "Core i5 12400F"),
    # AMD Ryzen specific
    (r"\b(ryzen\s*9\s*7950x(?:3d)?|ryzen\s*9\s*7900x(?:3d)?)\b", "AMD Ryzen 9 7000-series", 11500, "Ryzen 9 7900X"),
    (r"\b(ryzen\s*9\s*5950x|ryzen\s*9\s*5900x)\b", "AMD Ryzen 9 5000-series", 6800, "Ryzen 9 5900X"),
    (r"\b(ryzen\s*7\s*7800x3d)\b", "AMD Ryzen 7 7800X3D", 8800, "Ryzen 7 7800X3D"),
    (r"\b(ryzen\s*7\s*7700x?)\b", "AMD Ryzen 7 7700X", 6200, "Ryzen 7 7700X"),
    (r"\b(ryzen\s*7\s*5800x3d)\b", "AMD Ryzen 7 5800X3D", 6500, "Ryzen 7 5800X3D"),
    (r"\b(ryzen\s*7\s*5700x3d)\b", "AMD Ryzen 7 5700X3D", 5200, "Ryzen 7 5700X3D"),
    (r"\b(ryzen\s*7\s*5800x|ryzen\s*7\s*5700x|ryzen\s*7\s*3700x)\b", "AMD Ryzen 7 5700X/5800X", 4200, "Ryzen 7 5700X"),
    (r"\b(ryzen\s*5\s*7600x?)\b", "AMD Ryzen 5 7600X", 4600, "Ryzen 5 7600X"),
    (r"\b(ryzen\s*5\s*5600x?)\b", "AMD Ryzen 5 5600X", 2800, "Ryzen 5 5600X"),
    (r"\b(ryzen\s*5\s*3600x?)\b", "AMD Ryzen 5 3600", 1600, "Ryzen 5 3600"),
    # Generic families when specific sub-model is omitted (e.g. "ryzen 5", "core i7")
    (r"\b(ryzen\s*9)\b", "AMD Ryzen 9 (Family)", 8500, "Ryzen 9"),
    (r"\b(ryzen\s*7)\b", "AMD Ryzen 7 (Family)", 5200, "Ryzen 7"),
    (r"\b(ryzen\s*5)\b", "AMD Ryzen 5 (Family)", 3200, "Ryzen 5"),
    (r"\b(ryzen\s*3)\b", "AMD Ryzen 3 (Family)", 1500, "Ryzen 3"),
    (r"\b(core\s*i9|intel\s*i9|\bi9\b)\b", "Intel Core i9 (Family)", 8000, "Core i9"),
    (r"\b(core\s*i7|intel\s*i7|\bi7\b)\b", "Intel Core i7 (Family)", 5200, "Core i7"),
    (r"\b(core\s*i5|intel\s*i5|\bi5\b)\b", "Intel Core i5 (Family)", 3600, "Core i5"),
    (r"\b(core\s*i3|intel\s*i3|\bi3\b)\b", "Intel Core i3 (Family)", 1800, "Core i3"),
]


def _make_sample_url(query: str) -> str:
    encoded = urllib.parse.quote_plus(query.strip())
    return f"https://www.bazos.cz/search.php?hledat={encoded}"


def is_pc_build_candidate(title: str, description: str = "") -> bool:
    """Return True if text denotes a complete PC build or desktop gaming computer."""
    t = f"{title} {description}".lower()
    pc_keywords = (
        "herní pc", "herni pc", "gaming pc", "pc sestava", "stolní pc", "stolni pc",
        "počítač", "pocitac", "компьютер", "сборка пк", "системный блок", "gamebox",
        "game box", "desktop pc", "desktop", "mining rig",
    )
    if any(k in t for k in pc_keywords):
        return True
    has_gpu = any(k in t for k in ("rtx", "gtx", "geforce", "radeon rx", "rx 7", "rx 6"))
    has_cpu = any(k in t for k in ("ryzen", "core i", "intel i", "i5", "i7", "i9", "procesor"))
    return has_gpu and has_cpu


def _extract_ram_capacity_and_type(text: str) -> tuple[int | None, str]:
    """Extract RAM capacity in GB and memory generation (DDR4/DDR5) from text."""
    from nextlife.services.kit_ram_profile import extract_ram_details
    spec = extract_ram_details(text)
    if spec:
        return spec.capacity_gb, spec.generation
    return None, "DDR4/DDR5"


def _extract_storage_capacity(text: str) -> tuple[str | None, int]:
    """Extract storage capacity and price from text (e.g. 2TB, 1TB, 512GB, 1000G)."""
    t = text.lower()
    if re.search(r"\b(4\s*tb|4tb)\b", t):
        return "4TB NVMe SSD", 4800
    if re.search(r"\b(2\s*tb|2tb)\b", t):
        return "2TB NVMe SSD", 2600
    if re.search(r"\b(1\s*tb|1tb|1000\s*(?:gb|g))\b", t):
        return "1TB NVMe SSD", 1450
    if re.search(r"\b(512\s*(?:gb|g)|500\s*(?:gb|g))\b", t):
        return "512GB NVMe SSD", 750
    if re.search(r"\b(256\s*(?:gb|g))\b", t):
        return "256GB SSD", 450
    return None, 1450


def decompose_pc_build(
    title: str,
    description: str = "",
    total_cost: Decimal | None = None,
    connector: Any | None = None,
) -> list[dict[str, Any]]:
    """Profile-based decomposition for desktop computer builds.

    Adheres strictly to the ad text as the primary source of truth:
    - Never includes components excluded in text (e.g. 'bez grafiky', 'bez disku', 'bez ram').
    - Only includes components that are explicitly mentioned or physically integral to a complete PC.
    """
    from nextlife.services.kit_ram_profile import extract_ram_details, calculate_ram_price, RamSpec

    full_text = f"{title}\n{description}".strip()
    components: list[dict[str, Any]] = []

    # Detect negative context & exclusions (text is the single source of truth)
    is_gpu_excluded = bool(re.search(
        r"\b(bez\s*(?:grafik|gpu|grafické\s*karty|vga)|integrovaná\s*(?:grafika|grafická\s*karta|vga)|no\s*gpu|intel\s*uhd|radeon\s*graphics\s*\(integ)\b",
        full_text.lower(),
    ))
    is_cpu_excluded = bool(re.search(r"\b(bez\s*(?:procesoru|cpu)|no\s*cpu)\b", full_text.lower()))
    is_ram_excluded = bool(re.search(r"\b(bez\s*(?:ram|pamět|pamet|pamětí)|no\s*ram)\b", full_text.lower()))
    is_storage_excluded = bool(re.search(r"\b(bez\s*(?:disku|ssd|hdd|úložiště|uloziste|m\.?2)|no\s*storage|no\s*ssd|no\s*disk)\b", full_text.lower()))
    is_case_psu_excluded = bool(re.search(r"\b(bez\s*(?:zdroje|psu|skřín|skrine|case))\b", full_text.lower()))
    is_cooler_excluded = bool(re.search(r"\b(bez\s*(?:chladič|chladic|cooler|chlazení)|no\s*cooler)\b", full_text.lower()))
    is_upgrade_set = bool(re.search(r"\b(upgrade\s*kit|set\s*(?:deska|cpu|základní|zakladni)|bundle|combo|pouze\s*komponenty)\b", full_text.lower()))
    has_case_or_psu = bool(re.search(r"\b(zdroj|psu|skříň|skrin|case|bedna|seasonic|corsair|be\s*quiet|fortron|silverstone|fractal|nzxt\s*h\d|montech)\b", full_text.lower()))
    has_cooler_mentioned = bool(re.search(r"\b(chladič|chladic|cooler|chlazení|liquid|aio|vodn[íi]|kraken|noctua|arctic|wraith|assassin|dark\s*rock|endorfy|deepcool)\b", full_text.lower()))
    is_assembled_pc = any(k in full_text.lower() for k in ("herní pc", "herni pc", "gaming pc", "pc sestava", "stolní pc", "počítač", "gamebox"))

    # 1. GPU Extraction
    gpu_entry: dict[str, Any] | None = None
    t_inspect = full_text
    if not is_gpu_excluded:
        for pat, label, price, search_kw in _GPU_CATALOG:
            m = re.search(pat, t_inspect.lower())
            if m:
                gpu_entry = {
                    "name": f"Видеокарта (GPU): {label}",
                    "qty": 1,
                    "median_price": price,
                    "prices_count": 5,
                    "status": "ok",
                    "component_type": "gpu",
                    "_query": search_kw,
                    "sample_urls": [_make_sample_url(search_kw)],
                }
                t_inspect = re.sub(pat, " [GPU_EXCLUDED] ", t_inspect, flags=re.IGNORECASE)
                t_inspect = re.sub(r"\b(24|20|16|12|10|8|6)\s*(?:gb|g|гб)\s*(?:vram|gddr\d?)?\b", " ", t_inspect, count=1, flags=re.IGNORECASE)
                break

    if gpu_entry:
        components.append(gpu_entry)

    # 2. CPU Extraction
    cpu_entry: dict[str, Any] | None = None
    if not is_cpu_excluded:
        for pat, label, price, search_kw in _CPU_CATALOG:
            m = re.search(pat, t_inspect.lower())
            if m:
                cpu_entry = {
                    "name": f"Процессор (CPU): {label}",
                    "qty": 1,
                    "median_price": price,
                    "prices_count": 5,
                    "status": "ok",
                    "component_type": "cpu",
                    "_query": search_kw,
                    "sample_urls": [_make_sample_url(search_kw)],
                }
                t_inspect = re.sub(pat, " [CPU_EXCLUDED] ", t_inspect, flags=re.IGNORECASE)
                break

    if cpu_entry:
        components.append(cpu_entry)

    # 3. RAM Extraction: takes into account generation (DDR2-DDR5), frequency, form-factor (SODIMM/UDIMM)
    if not is_ram_excluded:
        cpu_ctx = cpu_entry["name"] if cpu_entry else full_text
        ram_spec = extract_ram_details(t_inspect, context=cpu_ctx)
        if not ram_spec:
            ram_spec = extract_ram_details(full_text, context=cpu_ctx)

        # Baseline only if an assembled PC is explicitly being sold
        if not ram_spec and is_assembled_pc and not is_upgrade_set:
            gen = _extract_ram_capacity_and_type(full_text)[1]
            ram_spec = RamSpec(capacity_gb=16, generation="DDR5" if "DDR5" in gen else "DDR4")

        if ram_spec:
            ram_price = calculate_ram_price(ram_spec)
            components.append({
                "name": f"Оперативная память (RAM): {ram_spec.display_name}",
                "qty": 1,
                "median_price": ram_price,
                "prices_count": 5,
                "status": "ok",
                "component_type": "ram",
                "_query": ram_spec.search_query,
                "sample_urls": [_make_sample_url(ram_spec.search_query)],
            })

    # 4. Storage Extraction: strictly verify storage presence in text
    if not is_storage_excluded:
        storage_label, storage_price = _extract_storage_capacity(full_text)
        is_turnkey_pc = bool(re.search(r"\b(stačí\s*zapojit|připraven|nainstalov[áa]n\s*win|windows\s*1[01]|kompletní\s*pc|hotový\s*pc)\b", full_text.lower()))
        if storage_label:
            components.append({
                "name": f"Накопитель (Storage): {storage_label}",
                "qty": 1,
                "median_price": storage_price,
                "prices_count": 5,
                "status": "ok",
                "component_type": "storage",
                "_query": storage_label[:14],
                "sample_urls": [_make_sample_url(storage_label[:14])],
            })
        elif is_turnkey_pc and is_assembled_pc and not is_upgrade_set:
            components.append({
                "name": "Накопитель (Storage): 1TB NVMe M.2 SSD",
                "qty": 1,
                "median_price": 1450,
                "prices_count": 5,
                "status": "ok",
                "component_type": "storage",
                "_query": "1TB SSD NVMe",
                "sample_urls": [_make_sample_url("1TB SSD NVMe")],
            })

    # 5. Motherboard, Case & Power Supply (Platform)
    if is_case_psu_excluded or (is_upgrade_set and not has_case_or_psu):
        if any(k in full_text.lower() for k in ("deska", "motherboard", "základní", "zakladni", "b550", "b650", "z790", "x570", "b450", "z690")):
            components.append({
                "name": "Материнская плата (Motherboard)",
                "qty": 1,
                "median_price": 2800,
                "prices_count": 5,
                "status": "ok",
                "component_type": "motherboard",
                "_query": "základní deska",
                "sample_urls": [_make_sample_url("základní deska")],
            })
    elif has_case_or_psu or (is_assembled_pc and not is_upgrade_set):
        gpu_price = gpu_entry["median_price"] if gpu_entry else 5000
        if gpu_price >= 18000:
            psu_desc, platform_price = "850W-1000W Gold", 5500
        elif gpu_price >= 10000:
            psu_desc, platform_price = "750W Bronze/Gold", 4200
        else:
            psu_desc, platform_price = "550W-650W", 3200

        components.append({
            "name": f"Материнская плата, корпус и блок питания ({psu_desc})",
            "qty": 1,
            "median_price": platform_price,
            "prices_count": 5,
            "status": "ok",
            "component_type": "platform",
            "_query": "skříň zdroj PC",
            "sample_urls": [_make_sample_url("skříň zdroj PC")],
        })

    # 6. CPU Cooling
    if not is_cooler_excluded:
        has_aio = bool(re.search(r"\b(liquid|aio|vodn[íi]|water|kraken|arctic liquid|сво)\b", full_text.lower()))
        if has_aio:
            components.append({
                "name": "Система жидкостного охлаждения (AIO Liquid Cooling)",
                "qty": 1,
                "median_price": 2400,
                "prices_count": 5,
                "status": "ok",
                "component_type": "cooling",
                "_query": "vodní chlazení AIO",
                "sample_urls": [_make_sample_url("vodní chlazení AIO")],
            })
        elif has_cooler_mentioned or (is_assembled_pc and not is_upgrade_set):
            components.append({
                "name": "Охлаждение процессора (CPU Cooler)",
                "qty": 1,
                "median_price": 750,
                "prices_count": 4,
                "status": "ok",
                "component_type": "cooling",
                "_query": "chladič CPU",
                "sample_urls": [_make_sample_url("chladič CPU")],
            })

    # Sample verification via connector with tight overpriced-ceiling cutoffs
    if connector is not None and components:
        from concurrent.futures import ThreadPoolExecutor

        def _enrich(c: dict[str, Any]) -> None:
            query = c.pop("_query", c["name"])
            fb = c["median_price"]
            c_type = c.get("component_type", "other")
            # Aggressive ceiling cutoffs to prevent overpriced listings from distorting valuation
            if c_type == "gpu":
                expected_range = (int(fb * 0.50), int(fb * 1.20))
            elif c_type == "cpu":
                expected_range = (int(fb * 0.50), int(fb * 1.30))
            elif c_type == "ram":
                expected_range = (int(fb * 0.40), int(fb * 1.40))
            else:
                expected_range = (int(fb * 0.40), int(fb * 1.50))
            try:
                raw_items = connector.search(query)[:10]
                item_dicts = [{"title": it.title, "price": it.price, "url": it.external_url} for it in raw_items]
                med, cnt, sample_urls = calculate_sample_median(
                    query=query,
                    items=item_dicts,
                    fallback_median=fb,
                    expected_price_range=expected_range,
                )
                if cnt >= 1 and sample_urls:
                    c["median_price"] = med
                    c["prices_count"] = cnt
                    c["sample_urls"] = sample_urls[:2] + [_make_sample_url(query)]
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=min(4, len(components))) as pool:
            list(pool.map(_enrich, components))
    else:
        for c in components:
            c.pop("_query", None)

    return components
