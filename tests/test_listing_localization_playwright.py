"""E2E Playwright verification for marketplace language localization."""

from pathlib import Path
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


def test_listing_localization_e2e():
    token = get_token()
    screenshots_dir = Path(".a/reports/screenshots")
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # Emulate mobile screen (iPhone 13 viewport 390x844)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
        )
        context.add_cookies([
            {
                "name": "nextlife_token",
                "value": token,
                "domain": "127.0.0.1",
                "path": "/",
            }
        ])
        page = context.new_page()

        # 1. Stock item 130 listings tab
        page.goto(f"{BASE_URL}/ui/stock/130#listings", wait_until="networkidle")
        page.wait_for_selector("[data-testid='stock-listings']")

        # Verify language badges are present in the DOM
        lang_badges = page.locator("[data-testid='listing-lang-badge']").all_text_contents()
        print(f"Discovered listing language badges: {lang_badges}")
        assert "SK" in lang_badges, "SK language badge missing"
        assert "PL" in lang_badges, "PL language badge missing"
        assert "DE" in lang_badges, "DE language badge missing"
        assert "EN" in lang_badges, "EN language badge missing"
        assert "CS" in lang_badges, "CS language badge missing"

        # Capture mobile overview of listings
        page.screenshot(path=str(screenshots_dir / "listing_languages_stock130.png"), full_page=True)

        # 2. Check Bazoš SK listing details
        page.goto(f"{BASE_URL}/ui/listings/1bbe5ee0-9669-434c-9a94-f29d2bbdf6f1", wait_until="networkidle")
        sk_desc = page.locator("textarea[name='description'], div.description-text, pre, [data-testid='listing-description']").first.text_content() or page.content()
        print("Bazoš SK text snippet:", sk_desc[:120])
        assert "Predám" in sk_desc or "predám" in sk_desc.lower(), f"Expected Slovak 'Predám', got: {sk_desc[:200]}"
        assert "ako nová" in sk_desc or "stave" in sk_desc, f"Expected Slovak condition, got: {sk_desc[:200]}"
        page.screenshot(path=str(screenshots_dir / "listing_bazos_sk_detail.png"), full_page=True)

        # 3. Check OLX PL listing details
        page.goto(f"{BASE_URL}/ui/listings/0c08af99-c119-4089-a3d1-f3bd519a6f3a", wait_until="networkidle")
        pl_desc = page.locator("textarea[name='description'], div.description-text, pre, [data-testid='listing-description']").first.text_content() or page.content()
        print("OLX PL text snippet:", pl_desc[:120])
        assert "Sprzedam" in pl_desc or "sprzedam" in pl_desc.lower(), f"Expected Polish 'Sprzedam', got: {pl_desc[:200]}"
        assert "jak nowa" in pl_desc, f"Expected Polish condition, got: {pl_desc[:200]}"
        page.screenshot(path=str(screenshots_dir / "listing_olx_pl_detail.png"), full_page=True)

        # 4. Check Kleinanzeigen DE listing details
        page.goto(f"{BASE_URL}/ui/listings/592595b6-162a-49ee-84a9-070df216eace", wait_until="networkidle")
        de_desc = page.locator("textarea[name='description'], div.description-text, pre, [data-testid='listing-description']").first.text_content() or page.content()
        print("Kleinanzeigen DE text snippet:", de_desc[:120])
        assert "verkaufe" in de_desc.lower(), f"Expected German 'verkaufe', got: {de_desc[:200]}"
        assert "wie neu" in de_desc or "neuwertig" in de_desc, f"Expected German condition, got: {de_desc[:200]}"
        page.screenshot(path=str(screenshots_dir / "listing_kleinanzeigen_de_detail.png"), full_page=True)

        browser.close()
        print("All E2E checks passed and screenshots saved!")


if __name__ == "__main__":
    test_listing_localization_e2e()
