"""Playwright and HTTP verification for Connectors and Auth Hub UI."""

import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from nextlife.core.config import load_config
from nextlife.core.secrets import resolve_secret

BASE_URL = "http://127.0.0.1:8081"


def get_token() -> str:
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("NEXTLIFE_WEB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    cfg = load_config()
    return resolve_secret(cfg.app.auth.web_token_secret_ref) or ""


def test_auth_hub_http():
    """Verify Connect screen returns HTTP 200 and expected markup."""
    token = get_token()
    cookies = {"nextlife_token": token}
    client = httpx.Client(base_url=BASE_URL, cookies=cookies, timeout=10.0)

    # 1. Main connect screen
    r = client.get("/ui/connect?platform=bazar_cz")
    assert r.status_code == 200
    assert "Центр авторизации площадок" in r.text
    assert "Bazar.cz" in r.text
    assert "Aukro.cz" in r.text
    assert "Сохранить сессию" in r.text
    assert "Открыть сайт для входа" in r.text

    # 2. Connectors map
    r = client.get("/ui/connectors")
    assert r.status_code == 200
    assert "Авторизоваться" in r.text

    # 3. Save valid JSON storage state
    dummy_state = {
        "cookies": [{"name": "test_sid", "value": "xyz123", "domain": "bazar.cz", "path": "/"}],
        "origins": []
    }
    r = client.post(
        "/ui/connect/bazar_cz/storage",
        data={"storage_json": json.dumps(dummy_state)},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "успешно сохранена и активирована" in r.text


def test_auth_hub_playwright():
    """E2E Playwright test with screenshot capture."""
    token = get_token()
    screenshots_dir = Path(".a/reports/screenshots")
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        # Add auth cookie
        context.add_cookies([
            {
                "name": "nextlife_token",
                "value": token,
                "domain": "127.0.0.1",
                "path": "/",
            }
        ])
        page = context.new_page()

        # 1. Connect screen for Bazar.cz
        page.goto(f"{BASE_URL}/ui/connect?platform=bazar_cz")
        page.wait_for_selector("[data-testid='connect-hub']")
        page.screenshot(path=str(screenshots_dir / "auth_hub_bazar.png"))

        # 2. Switch to Aukro.cz tab
        page.click("[data-testid='platform-nav-aukro_cz']")
        page.wait_for_url("**/ui/connect?platform=aukro_cz")
        page.screenshot(path=str(screenshots_dir / "auth_hub_aukro.png"))

        # 3. Connectors map screen
        page.goto(f"{BASE_URL}/ui/connectors")
        page.wait_for_selector("[data-testid='connectors-screen']")
        page.screenshot(path=str(screenshots_dir / "connectors_map_screen.png"))

        # 4. Stock Item 130 screen
        page.goto(f"{BASE_URL}/ui/stock/130#listings")
        page.wait_for_selector("[data-testid='stock-listings']")
        page.screenshot(path=str(screenshots_dir / "stock_item_130_listings.png"))

        browser.close()
