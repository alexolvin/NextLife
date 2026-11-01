"""NextLife Version and UI Color Cycle Module.

Format: <year>.<month>.<revision>
- Revision increments on any project changes.
- Revision resets to 0 when transitioning to a new month.
- UI dark accent/header color cycles based on revision.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
VERSION_JSON_PATH = ROOT_DIR / "config" / "version.json"

# Palette cycle matching perceived luminance/density of rgb(10, 61, 122):
# L ≈ 52-58 (authoritative, high contrast with white text).
THEME_PALETTE = [
    {
        "name": "Navy Blue",
        "description": "Классический глубокий синий (исходный)",
        "hex": "#0a3d7a",
        "rgb": "rgb(10, 61, 122)",
        "rgb_raw": "10, 61, 122",
        "hex_hover": "#072c5a",
        "hex_light": "#eff6ff",
        "hex_ring": "#bfdbfe",
    },
    {
        "name": "Forest Green",
        "description": "Глубокий изумрудно-зелёный",
        "hex": "#0e6239",
        "rgb": "rgb(14, 98, 57)",
        "rgb_raw": "14, 98, 57",
        "hex_hover": "#0a482a",
        "hex_light": "#ecfdf5",
        "hex_ring": "#a7f3d0",
    },
    {
        "name": "Carrot Rust",
        "description": "Насыщенный морковно-терракотовый",
        "hex": "#a83e0c",
        "rgb": "rgb(168, 62, 12)",
        "rgb_raw": "168, 62, 12",
        "hex_hover": "#822f08",
        "hex_light": "#fff7ed",
        "hex_ring": "#fed7aa",
    },
    {
        "name": "Deep Crimson",
        "description": "Благородный бордово-винный",
        "hex": "#801432",
        "rgb": "rgb(128, 20, 50)",
        "rgb_raw": "128, 20, 50",
        "hex_hover": "#600e25",
        "hex_light": "#fdf2f8",
        "hex_ring": "#fbcfe8",
    },
    {
        "name": "Deep Violet",
        "description": "Тёмный насыщенный индиго/фиолетовый",
        "hex": "#481e78",
        "rgb": "rgb(72, 30, 120)",
        "rgb_raw": "72, 30, 120",
        "hex_hover": "#35155a",
        "hex_light": "#f5f3ff",
        "hex_ring": "#ddd6fe",
    },
    {
        "name": "Deep Teal",
        "description": "Тёмная морская волна / бирюза",
        "hex": "#0c5a6e",
        "rgb": "rgb(12, 90, 110)",
        "rgb_raw": "12, 90, 110",
        "hex_hover": "#084252",
        "hex_light": "#f0fdfa",
        "hex_ring": "#99f6e4",
    },
    {
        "name": "Deep Bronze",
        "description": "Тёмно-бронзовый / благородный коричневый",
        "hex": "#6e4114",
        "rgb": "rgb(110, 65, 20)",
        "rgb_raw": "110, 65, 20",
        "hex_hover": "#52300e",
        "hex_light": "#fefce8",
        "hex_ring": "#fef08a",
    },
]


def load_version_info() -> dict[str, Any]:
    """Load current version dictionary from config/version.json with safe fallback."""
    if VERSION_JSON_PATH.is_file():
        try:
            with open(VERSION_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "year" in data and "month" in data and "revision" in data:
                    return data
        except Exception:
            pass

    # Default initial fallback
    now = datetime.now(timezone.utc)
    return {
        "year": now.year,
        "month": now.month,
        "revision": 0,
        "version": f"{now.year}.{now.month}.0",
    }


def get_version_str() -> str:
    """Return formatted version string: YYYY.MM.REV."""
    info = load_version_info()
    return f"{info['year']}.{info['month']}.{info['revision']}"


def get_current_theme(revision: int | None = None) -> dict[str, str]:
    """Return the theme palette dictionary for the given revision (or current revision)."""
    if revision is None:
        info = load_version_info()
        revision = int(info.get("revision", 0))
    idx = revision % len(THEME_PALETTE)
    return THEME_PALETTE[idx]


__version__ = get_version_str()
