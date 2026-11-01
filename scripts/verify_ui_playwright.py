#!/usr/bin/env python3
"""Automated verification and visual regression testing using Playwright."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
lib_path = str(ROOT_DIR / "lib" / "playwright_libs")
if Path(lib_path).exists():
    existing_ld = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{lib_path}:{existing_ld}".strip(":")

BASE_URL = os.environ.get("NEXTLIFE_BASE_URL", "http://127.0.0.1:8081")
TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
SCREENSHOT_DIR = Path(".a/tmp/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

print(f"=== Playwright UI Test starting for {BASE_URL} ===")

with sync_playwright() as p:
    browser = p.chromium.launch()
    # Emulate modern mobile device (390x844 - iPhone / modern Android)
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
    )
    page = context.new_page()

    # Step 0: Test Unauthenticated Browser Flow & UI Login Screen
    print(f"[0/7] Testing unauthenticated redirect to login screen...")
    resp_init = page.goto(f"{BASE_URL}/ui", wait_until="networkidle")
    assert "/ui/login" in page.url, f"Expected redirect to /ui/login, got {page.url}"
    login_html = page.content()
    assert "NextLife Re-Commerce" in login_html
    assert 'id="token-input"' in login_html
    page.screenshot(path=str(SCREENSHOT_DIR / "00_login_form.png"), full_page=True)
    print(" -> Screenshot saved: 00_login_form.png")

    # Test invalid token submission
    print(" -> Testing invalid token submission...")
    page.fill("#token-input", "invalid_token_sample_123")
    with page.expect_navigation():
        page.click("button[type='submit']")
    assert "/ui/login" in page.url
    err_html = page.content()
    assert "Неверный токен безопасности" in err_html
    print(" -> Friendly error message verified on invalid token submission")

    # Test successful login submission
    print(" -> Submitting valid security token...")
    page.fill("#token-input", TOKEN)
    with page.expect_navigation():
        page.click("button[type='submit']")
    assert "/ui/items" in page.url, f"Expected redirect to /ui/items, got {page.url}"
    print(" -> Successfully logged in via UI and redirected to /ui/items")

    # Step 1: Verify Feed Content
    print("[1/7] Verifying Feed contents...")
    content = page.content()

    # Check that authentic items are present
    assert "#777" in content, "Item #777 (ThinkPad) missing from feed"
    assert "#802" in content, "Item #802 (iPhone) missing from feed"
    assert "#348" in content, "Item #348 (Helios) missing from feed"
    print(" -> All 3 showcase items verified in feed (#777, #802, #348)")

    # Check that photos physically loaded without broken images
    broken_images = page.evaluate("""() => {
        const imgs = Array.from(document.querySelectorAll('img.feed-thumb'));
        return imgs.filter(img => !img.complete || img.naturalWidth === 0).map(img => img.src);
    }""")
    assert len(broken_images) == 0, f"Feed thumbnails failed to load: {broken_images}"
    print(" -> Verified feed thumbnails loaded successfully (0 broken images)")

    # Test "Обновить" (Refresh) button click
    print(" -> Testing 'Обновить' button in header...")
    refresh_btn = page.query_selector("#btn-refresh")
    assert refresh_btn, "Header refresh button missing"
    with page.expect_navigation():
        refresh_btn.click()
    print(" -> Refresh button triggered successful reload and sync!")

    page.screenshot(path=str(SCREENSHOT_DIR / "01_items_feed.png"), full_page=True)
    print(" -> Screenshot saved: 01_items_feed.png")

    # Step 2: Open Item Card #777 (ThinkPad P14s)
    card_url = f"{BASE_URL}/ui/items/777"
    print(f"[2/6] Navigating to Item Card #777: {card_url}")
    resp = page.goto(card_url, wait_until="networkidle")
    assert resp and resp.status == 200, f"Item card returned {resp.status if resp else None}"
    card_content = page.content()
    assert "ThinkPad P14s" in card_content, "ThinkPad title missing"
    assert "Ryzen 7 PRO 8840HS" in card_content, "Specs missing"
    assert "Фотогалерея (3)" in card_content, "Photo gallery count mismatch"
    assert "Дефекты" in card_content, "Defects section missing"
    assert "bottom_scuff" in card_content, "Defect bottom_scuff missing"
    assert "bazos_cz" in card_content, "Bazos listing missing"

    # Verify that gallery photos loaded with naturalWidth > 0 and no 'Нет превью'
    assert "Нет превью" not in card_content, "Found 'Нет превью' in photo gallery"
    broken_gallery = page.evaluate("""() => {
        const imgs = Array.from(document.querySelectorAll('.photo-cell img'));
        return imgs.filter(img => !img.complete || img.naturalWidth === 0).map(img => img.src);
    }""")
    assert len(broken_gallery) == 0, f"Gallery images failed to load: {broken_gallery}"
    print(" -> Verified all 3 gallery photos loaded (0 broken images, 0 'Нет превью')")

    page.screenshot(path=str(SCREENSHOT_DIR / "02_item_card.png"), full_page=True)
    print(" -> Screenshot saved: 02_item_card.png")

    # Step 3: Open Draft Approval Screen for #777
    draft_url = f"{BASE_URL}/ui/draft/777"
    print(f"[3/6] Navigating to Draft Approval: {draft_url}")
    resp = page.goto(draft_url, wait_until="networkidle")
    assert resp and resp.status == 200, f"Draft screen returned {resp.status if resp else None}"
    draft_content = page.content()
    assert "Утверждение черновика" in draft_content
    assert "HASH (SHA-256)" in draft_content
    assert "bazos_cz" in draft_content

    # Verify draft photos loaded
    broken_draft = page.evaluate("""() => {
        const imgs = Array.from(document.querySelectorAll('img[alt="Kit"]'));
        return imgs.filter(img => !img.complete || img.naturalWidth === 0).map(img => img.src);
    }""")
    assert len(broken_draft) == 0, f"Draft kit images failed to load: {broken_draft}"
    print(" -> Verified draft kit photos loaded successfully")

    page.screenshot(path=str(SCREENSHOT_DIR / "03_draft_approval.png"), full_page=True)
    print(" -> Screenshot saved: 03_draft_approval.png")

    # Step 4: Click Approve Form Button & Verify Redirect + Banner
    print("[4/6] Submitting approval form for #777...")
    with page.expect_navigation():
        page.click("#btn-approve")

    print(f" -> Current URL after click: {page.url}")
    assert "notice=approved" in page.url or page.url.endswith("/ui/items/777")
    content_after = page.content()
    assert "Черновик публикации успешно утверждён" in content_after or "APPROVED" in content_after
    page.screenshot(path=str(SCREENSHOT_DIR / "04_draft_approved.png"), full_page=True)
    print(" -> Screenshot saved: 04_draft_approved.png")

    # Step 5: Open Inbox Screen
    inbox_url = f"{BASE_URL}/ui/inbox"
    print(f"[5/6] Navigating to Inbox: {inbox_url}")
    resp = page.goto(inbox_url, wait_until="networkidle")
    assert resp and resp.status == 200
    inbox_content = page.content()
    assert "Petr Svoboda" in inbox_content or "iPhone 13 Pro" in inbox_content
    page.screenshot(path=str(SCREENSHOT_DIR / "05_inbox.png"), full_page=True)
    print(" -> Screenshot saved: 05_inbox.png")

    # Step 6: Open Deals & Archive Screen
    archive_url = f"{BASE_URL}/ui/archive"
    print(f"[6/6] Navigating to Deals/Archive: {archive_url}")
    resp = page.goto(archive_url, wait_until="networkidle")
    assert resp and resp.status == 200
    archive_content = page.content()
    assert "COMPLETED" in archive_content
    page.screenshot(path=str(SCREENSHOT_DIR / "06_archive.png"), full_page=True)
    print(" -> Screenshot saved: 06_archive.png")

    browser.close()

print("=== All Playwright UI tests completed successfully! ===")
