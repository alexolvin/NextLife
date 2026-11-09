"""RAM profile analyzer: generation, frequency, form-factor, and market valuation.

Rule K-1: Module must strictly be <= 400 lines.
Accurately assesses RAM value based on:
1. Capacity in GB (8, 16, 24, 32, 48, 64, 96, 128 GB)
2. Generation (DDR2, DDR3, DDR4, DDR5) - explicit or inferred from platform/CPU
3. Frequency (MHz: e.g. 6000, 3600, 3200, 1600, 800)
4. Form-factor: SODIMM (laptops, mini-PCs) vs UDIMM (desktop PCs)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class RamSpec:
    capacity_gb: int
    generation: str  # "DDR5", "DDR4", "DDR3", "DDR2"
    frequency_mhz: int | None = None
    form_factor: str = "UDIMM"  # "UDIMM" or "SODIMM"
    is_inferred_gen: bool = False

    @property
    def display_name(self) -> str:
        parts = [f"{self.capacity_gb}GB", self.generation]
        if self.frequency_mhz:
            parts.append(f"{self.frequency_mhz}MHz")
        if self.form_factor == "SODIMM":
            parts.append("SODIMM (Laptop/Mini-PC)")
        return " ".join(parts)

    @property
    def search_query(self) -> str:
        parts = [f"{self.capacity_gb}GB", self.generation]
        if self.frequency_mhz and self.frequency_mhz >= 3200:
            parts.append(f"{self.frequency_mhz}MHz")
        if self.form_factor == "SODIMM":
            parts.append("SODIMM")
        return " ".join(parts)


# Baseline secondary market prices in CZK (used hardware)
_BASE_RAM_PRICES = {
    "DDR5": {16: 1100, 32: 2200, 48: 3400, 64: 4200, 96: 6500, 128: 7500},
    "DDR4": {8: 380, 16: 800, 32: 1600, 64: 3200, 128: 6500},
    "DDR3": {4: 120, 8: 250, 16: 550, 32: 1200},
    "DDR2": {2: 60, 4: 150, 8: 280},
}


def infer_generation_from_context(context_text: str) -> str:
    """Infer DDR generation from CPU / motherboard chipset when not explicitly stated."""
    t = context_text.lower()
    # AM5 platform (Ryzen 7000/8000/9000) only supports DDR5
    if re.search(r"\b(ryzen\s*[79]\s*7\d{3}|ryzen\s*5\s*7[56]00|ryzen\s*\d\s*[89]\d{3}|b650|x670|a620|x870|z790|b760)\b", t):
        return "DDR5"
    # AM4 platform (Ryzen 1000-5000) and Intel 6th-12th gen use DDR4
    if re.search(r"\b(ryzen\s*\d\s*[12345]\d{3}|b450|b550|x570|a320|x470|i[3579]-?1[012]\d{3}|i[3579]-?[6789]\d{3}|z490|b460|b560|z590|z690)\b", t):
        return "DDR4"
    # Intel 2nd-4th gen (Core i-2000 to i-4000) and AMD AM3/FX use DDR3
    if re.search(r"\b(i[3579]-?[234]\d{3}|fx-?\d{4}|phenom|am3\+?|h81|b85|z97|h61|h77|z77)\b", t):
        return "DDR3"
    # Intel Core 2 Duo / Quad, LGA 775, AMD AM2 use DDR2
    if re.search(r"\b(core\s*2\s*(?:duo|quad)|q6600|q9550|e8400|lga\s*775|am2\+?)\b", t):
        return "DDR2"
    # Default to DDR4 for modern systems if unspecified
    return "DDR4"


def extract_ram_details(text: str, context: str = "") -> RamSpec | None:
    """Extract complete RAM specifications: capacity, generation, frequency, form-factor."""
    full_t = f"{text} {context}".lower()

    # Form-factor detection
    is_sodimm = bool(re.search(r"\b(so-?dimm|notebook|ntb|laptop|pro\s*ntb|do\s*notebooku|mini\s*pc)\b", full_t))
    form_factor = "SODIMM" if is_sodimm else "UDIMM"

    # Generation detection
    gen = None
    m_gen = re.search(r"\bddr([2345])\b", full_t)
    if m_gen:
        gen = f"DDR{m_gen.group(1)}"
        is_inferred = False
    else:
        gen = infer_generation_from_context(full_t)
        is_inferred = True

    # Frequency detection (MHz)
    freq = None
    m_freq = re.search(r"\b([1-8]\d{3}|800|667|533|400)\s*(?:mhz|mt/s)\b", full_t)
    if not m_freq:
        # Check DDR + frequency (e.g. "DDR4 3200", "DDR5 6000", "DDR3 1600", "DDR2 800")
        m_freq = re.search(r"\bddr[2345]\s*([1-8]\d{3}|800|667|533|400)\b", full_t)
    if m_freq:
        f_val = int(m_freq.group(1))
        # Validate frequency ranges
        if 400 <= f_val <= 8400:
            freq = f_val

    # Capacity detection
    cap = None

    # Multi-stick patterns: e.g. "2x16gb", "4x8g", "2x8 gb", "2x32гб"
    m_multi = re.search(r"\b([1248])\s*[x*]\s*(\d{1,3})\s*(?:gb|g|гб)?\b", text.lower())
    if m_multi:
        sticks, stick_cap = int(m_multi.group(1)), int(m_multi.group(2))
        total = sticks * stick_cap
        if total in (2, 4, 8, 16, 24, 32, 48, 64, 96, 128):
            cap = total

    # Explicit ram label: "ram 32g", "32gb ram", "pamet 16gb", "ram: 32 gb"
    if cap is None:
        m_label = re.search(r"\b(?:ram|paměť|pamet|operační\s*paměť)\s*[:=]?\s*(\d{1,3})\s*(?:gb|g|гб)?\b", text.lower())
        if m_label:
            val = int(m_label.group(1))
            if val in (2, 4, 8, 16, 24, 32, 48, 64, 96, 128):
                cap = val

    if cap is None:
        m_label2 = re.search(r"\b(\d{1,3})\s*(?:gb|g|гб)?\s*(?:ram|paměť|pamet)\b", text.lower())
        if m_label2:
            val = int(m_label2.group(1))
            if val in (2, 4, 8, 16, 24, 32, 48, 64, 96, 128):
                cap = val

    # Token-based detection: "32G", "16GB", "32gb"
    if cap is None:
        tokens = re.findall(r"\b(128|96|64|48|32|24|16|8|4|2)\s*(?:gb|g|гб)\b", text.lower())
        for tok in tokens:
            val = int(tok)
            cap = val
            break

    if cap is None:
        return None

    return RamSpec(
        capacity_gb=cap,
        generation=gen,
        frequency_mhz=freq,
        form_factor=form_factor,
        is_inferred_gen=is_inferred,
    )


def calculate_ram_price(spec: RamSpec) -> int:
    """Calculate realistic secondary market price in CZK based on full spec."""
    gen_prices = _BASE_RAM_PRICES.get(spec.generation, _BASE_RAM_PRICES["DDR4"])
    price = gen_prices.get(spec.capacity_gb)
    if price is None:
        # Interpolate or fallback
        if spec.capacity_gb <= 4:
            price = 150
        elif spec.capacity_gb <= 8:
            price = 350
        elif spec.capacity_gb <= 16:
            price = 800
        elif spec.capacity_gb <= 32:
            price = 1600
        else:
            price = 3200

    # Frequency premium adjustment
    if spec.frequency_mhz:
        if spec.generation == "DDR4" and spec.frequency_mhz >= 3600:
            price = int(price * 1.20)
        elif spec.generation == "DDR5" and spec.frequency_mhz >= 6000:
            price = int(price * 1.25)

    # Form-factor adjustment (SODIMM DDR3/DDR4 slightly higher on secondary market)
    if spec.form_factor == "SODIMM" and spec.generation in ("DDR3", "DDR4"):
        price = int(price * 1.15)

    return max(50, price)
