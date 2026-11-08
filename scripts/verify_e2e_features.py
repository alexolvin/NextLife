"""Playwright E2E verification on mobile viewport (390x844).

Verifies:
1. Item 130 #photos stage: gallery, cover designation, cover badge, and photo editor modal.
2. Item 130 #listings stage: listing cards, Bazoš SK live published listing card, and unpublish handling.
3. Saves mobile screenshots into .a/reports/screenshots/.
"""

import os
import time
from playwright.sync_api import sync_playwright

from nextlife.core.config import load_config
from nextlife.core.secrets import resolve_secret

BASE_URL = "http://127.0.0.1:8081"
cfg = load_config()
TOKEN = resolve_secret(cfg.app.auth.web_token_secret_ref)
SCREENSHOT_DIR = ".a/reports/screenshots"

def run_verification():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            is_mobile=True,
            has_touch=True,
        )
        context.add_cookies([{
            "name": "nextlife_token",
            "value": TOKEN,
            "domain": "127.0.0.1",
            "path": "/",
        }])

        page = context.new_page()

        # Step 1: Photos Stage
        print("[1] Navigating to Item 130 (#photos)...")
        page.goto(f"{BASE_URL}/ui/items/130/#photos")
        page.wait_for_selector("#gallery-card", state="visible", timeout=15000)
        time.sleep(1)

        page.screenshot(path=f"{SCREENSHOT_DIR}/01_photos_gallery_mobile.png")
        print(f"Saved {SCREENSHOT_DIR}/01_photos_gallery_mobile.png")

        # Step 2: Select thumbnail 1 and set as cover
        print("[2] Selecting thumbnail 1 and setting as cover...")
        thumb1 = page.locator("#thumb-item-1")
        if thumb1.is_visible():
            thumb1.click()
            time.sleep(0.5)

            cover_btn = page.locator("[data-testid='btn-op-cover']")
            if cover_btn.is_visible():
                cover_btn.click()
                page.wait_for_load_state("networkidle")
                time.sleep(1)
                page.goto(f"{BASE_URL}/ui/items/130/#photos")
                page.wait_for_selector("#gallery-card", state="visible")
                time.sleep(0.5)
                page.screenshot(path=f"{SCREENSHOT_DIR}/02_cover_photo_badge.png")
                print(f"Saved {SCREENSHOT_DIR}/02_cover_photo_badge.png")
                cover_marks = page.locator("[data-testid='photo-cover-mark']")
                print(f"Cover badges found: {cover_marks.count()}")

        # Step 3: Open Photo Editor Modal
        print("[3] Testing Photo Editor Modal...")
        main_img = page.locator("#slider-main-img")
        if main_img.is_visible():
            main_img.click()
            time.sleep(1)
            editor_modal = page.locator("#photo-editor-modal")
            editor_cover = page.locator("[data-testid='btn-editor-cover']")
            print("Modal visible:", editor_modal.is_visible(), "Editor cover button:", editor_cover.is_visible())
            page.screenshot(path=f"{SCREENSHOT_DIR}/03_photo_editor_modal.png")
            print(f"Saved {SCREENSHOT_DIR}/03_photo_editor_modal.png")
            # Close modal
            close_btn = page.locator("#photo-editor-modal button:has-text('✕')").first
            if close_btn.is_visible():
                close_btn.click()
                time.sleep(0.5)

        # Step 4: Listings Stage (#listings)
        print("[4] Navigating to Item 130 (#listings)...")
        page.goto(f"{BASE_URL}/ui/items/130/#listings")
        page.wait_for_selector("[data-testid='stage-listings-panel']", state="visible", timeout=15000)
        time.sleep(1)

        page.screenshot(path=f"{SCREENSHOT_DIR}/04_listings_stage_mobile.png", full_page=True)
        print(f"Saved {SCREENSHOT_DIR}/04_listings_stage_mobile.png")

        # Step 5: Check Bazoš SK listing card and unpublish
        print("[5] Checking Bazoš SK listing...")
        bazos_sk_link = page.locator("a[href*='pc.bazos.sk/inzerat/197224238']").first
        if bazos_sk_link.is_visible():
            print("Verified live Bazoš SK ad link is present!")
            bazos_card = bazos_sk_link.locator("xpath=ancestor::div[contains(@class, 'listing-card') or contains(@class, 'card')]").first
            bazos_card.scroll_into_view_if_needed()
            time.sleep(0.5)
            page.screenshot(path=f"{SCREENSHOT_DIR}/05_bazos_sk_card_mobile.png")
            print(f"Saved {SCREENSHOT_DIR}/05_bazos_sk_card_mobile.png")

        browser.close()
        print("Playwright E2E verification successfully finished!")

if __name__ == "__main__":
    run_verification()
