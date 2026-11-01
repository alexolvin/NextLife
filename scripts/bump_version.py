#!/usr/bin/env python3
"""Automatic version bump and UI theme rotation tool for NextLife.

Format: <year>.<month>.<revision>
- Revision increments on any project changes.
- Revision resets to 0 when transitioning to a new month.
- UI dark accent/header color cycles according to revision % len(palette).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nextlife.version import THEME_PALETTE, VERSION_JSON_PATH, get_current_theme, load_version_info


def update_css(theme: dict[str, str]) -> None:
    css_path = ROOT / "src" / "nextlife" / "api" / "static" / "app.css"
    if not css_path.is_file():
        return

    content = css_path.read_text(encoding="utf-8")

    # Update CSS variables in :root
    content = re.sub(
        r"--c-accent:\s*#[0-9a-fA-F]{6};",
        f"--c-accent:        {theme['hex']};",
        content,
    )
    content = re.sub(
        r"--c-accent-hov:\s*#[0-9a-fA-F]{6};",
        f"--c-accent-hov:    {theme['hex_hover']};",
        content,
    )
    content = re.sub(
        r"--c-accent-light:\s*#[0-9a-fA-F]{6};",
        f"--c-accent-light:  {theme['hex_light']};",
        content,
    )
    content = re.sub(
        r"--c-accent-ring:\s*#[0-9a-fA-F]{6};",
        f"--c-accent-ring:   {theme['hex_ring']};",
        content,
    )
    content = re.sub(
        r"--c-header:\s*#[0-9a-fA-F]{6};",
        f"--c-header:        {theme['hex']};",
        content,
    )
    content = re.sub(
        r"--c-header-2:\s*#[0-9a-fA-F]{6};",
        f"--c-header-2:      {theme['hex_hover']};",
        content,
    )

    # Update box-shadow focus rings
    content = re.sub(
        r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0\.1\s*\)",
        f"rgba({theme['rgb_raw']}, 0.1)",
        content,
    )

    css_path.write_text(content, encoding="utf-8")


def update_readme(version_str: str) -> None:
    # Update README.md (English)
    readme_en = ROOT / "README.md"
    if readme_en.is_file():
        text = readme_en.read_text(encoding="utf-8")
        if "**Version:**" in text:
            text = re.sub(r"\*\*Version:\*\*\s*[0-9.]+", f"**Version:** {version_str}", text)
        else:
            text = re.sub(
                r"(\*\*Languages:\*\* [^\n]+\n\n)",
                r"\1**Version:** " + version_str + "\n\n",
                text,
            )
        readme_en.write_text(text, encoding="utf-8")

    # Update README.ru.md (Russian)
    readme_ru = ROOT / "README.ru.md"
    if readme_ru.is_file():
        text = readme_ru.read_text(encoding="utf-8")
        if "**Версия:**" in text:
            text = re.sub(r"\*\*Версия:\*\*\s*[0-9.]+", f"**Версия:** {version_str}", text)
        else:
            text = re.sub(
                r"(\*\*Языки:\*\* [^\n]+\n\n)",
                r"\1**Версия:** " + version_str + "\n\n",
                text,
            )
        readme_ru.write_text(text, encoding="utf-8")


def update_pyproject(version_str: str) -> None:
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        text = re.sub(r'(\n\[project\]\n(?:[^\n]+\n)*?version\s*=\s*)"[^"]+"', r'\g<1>"' + version_str + '"', text)
        pyproject.write_text(text, encoding="utf-8")


def bump_version(force_rev: int | None = None) -> tuple[str, dict[str, str]]:
    current = load_version_info()
    now = datetime.now(timezone.utc)

    saved_year = current.get("year", now.year)
    saved_month = current.get("month", now.month)
    saved_rev = current.get("revision", 0)

    # Monthly reset rule: if current month/year is newer than saved, reset revision to 0
    if (now.year, now.month) > (saved_year, saved_month):
        new_year = now.year
        new_month = now.month
        new_rev = 0
    else:
        new_year = saved_year
        new_month = saved_month
        new_rev = saved_rev + 1

    if force_rev is not None:
        new_rev = force_rev

    version_str = f"{new_year}.{new_month}.{new_rev}"
    theme = get_current_theme(new_rev)

    info = {
        "year": new_year,
        "month": new_month,
        "revision": new_rev,
        "version": version_str,
        "theme": theme["name"],
        "accent_hex": theme["hex"],
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    VERSION_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VERSION_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.write("\n")

    update_css(theme)
    update_readme(version_str)
    update_pyproject(version_str)

    return version_str, theme


def main() -> None:
    parser = argparse.ArgumentParser(description="NextLife Version and Theme Manager")
    parser.add_argument("--check", action="store_true", help="Print current version and theme info")
    parser.add_argument("--print-version", action="store_true", help="Print only version string")
    parser.add_argument("--revision", type=int, default=None, help="Force specific revision")

    args = parser.parse_args()

    if args.print_version:
        info = load_version_info()
        print(f"{info['year']}.{info['month']}.{info['revision']}")
        return

    if args.check:
        info = load_version_info()
        theme = get_current_theme(info.get("revision", 0))
        print(f"Current NextLife Version: {info['year']}.{info['month']}.{info['revision']}")
        print(f"Theme Name:  {theme['name']} ({theme['description']})")
        print(f"Accent Color: {theme['hex']} ({theme['rgb']})")
        return

    ver, theme = bump_version(force_rev=args.revision)
    print(f"Bumped NextLife Version -> {ver}")
    print(f"Active UI Theme         -> {theme['name']} [{theme['hex']} / {theme['rgb']}]")


if __name__ == "__main__":
    main()
