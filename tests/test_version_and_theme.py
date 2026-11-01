"""Tests for versioning format, monthly reset, theme color cycling, and UI display."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from nextlife.api.main import create_app
from nextlife.version import THEME_PALETTE, get_current_theme, get_version_str, load_version_info
from scripts.bump_version import bump_version, update_css, update_pyproject, update_readme


def test_version_format():
    """Version string must adhere strictly to <year>.<month>.<revision> format."""
    ver = get_version_str()
    parts = ver.split(".")
    assert len(parts) == 3, f"Expected 3 parts, got {ver}"
    year, month, rev = parts
    assert year.isdigit() and len(year) == 4
    assert month.isdigit() and 1 <= int(month) <= 12
    assert rev.isdigit() and int(rev) >= 0


def test_color_cycle_sequence():
    """Dark color cycle rotates through distinct, high-contrast shades."""
    # Palette has at least 5 rich colors
    assert len(THEME_PALETTE) >= 5

    t0 = get_current_theme(0)
    assert t0["hex"] == "#0a3d7a"
    assert t0["rgb"] == "rgb(10, 61, 122)"

    t1 = get_current_theme(1)
    assert t1["hex"] == "#0e6239"
    assert "зелёный" in t1["description"].lower()

    t2 = get_current_theme(2)
    assert t2["hex"] == "#a83e0c"
    assert "морков" in t2["description"].lower()

    # Cycle wraps around smoothly
    n = len(THEME_PALETTE)
    assert get_current_theme(n)["hex"] == t0["hex"]
    assert get_current_theme(n + 1)["hex"] == t1["hex"]


def test_monthly_reset_rule(tmp_path):
    """When month advances, revision resets to 0."""
    from scripts.bump_version import bump_version

    # Simulate previous version from earlier month (e.g. 2026.10.5)
    with patch("scripts.bump_version.load_version_info") as mock_info:
        mock_info.return_value = {"year": 2026, "month": 10, "revision": 5}
        with patch("scripts.bump_version.datetime") as mock_dt:
            from datetime import datetime, timezone
            mock_dt.now.return_value = datetime(2026, 11, 1, tzinfo=timezone.utc)
            mock_dt.timezone = timezone

            ver, theme = bump_version(write_files=False)
            assert ver == "2026.11.0"
            assert theme["hex"] == "#0a3d7a"


def test_settings_page_first_line_version():
    """Settings page must render version in the first line under data-testid='settings-version'."""
    from nextlife.api.main import create_app
    from nextlife.core.config import load_config
    from nextlife.core.secrets import resolve_secret

    cfg = load_config()
    token = resolve_secret(cfg.app.auth.web_token_secret_ref) or "test-token"

    client = TestClient(create_app(), cookies={"nextlife_token": token})
    resp = client.get("/ui/settings?tab=profile")
    assert resp.status_code == 200
    html = resp.text

    # Verify banner exists
    assert 'data-testid="settings-version"' in html
    ver = get_version_str()
    assert f"Версия: {ver}" in html

    # Verify version appears before main tab content or form
    idx_ver = html.find('data-testid="settings-version"')
    idx_tabs = html.find('data-testid="settings-tabs"')
    assert idx_ver != -1
    assert idx_tabs != -1
    assert idx_ver < idx_tabs, "Version must appear in the first line before settings tabs"


def test_readme_version_display():
    """README.md and README.ru.md must display the current version."""
    root = Path(__file__).resolve().parent.parent
    ver = get_version_str()

    readme_en = (root / "README.md").read_text(encoding="utf-8")
    assert f"**Version:** {ver}" in readme_en

    readme_ru = (root / "README.ru.md").read_text(encoding="utf-8")
    assert f"**Версия:** {ver}" in readme_ru
