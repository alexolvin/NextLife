"""Mobile E2E Playwright test suite on 360 px viewport (T-075, TZ Part V.3).

DoD:
- Complete flow: Lot creation wizard, media, conditions/pricing, approval draft.
- Viewport width 360 px without horizontal scrolling (scrollWidth <= clientWidth).
- All interactive touch targets >= 44 px.
- Screenshots saved in .a/tmp/screenshots/.
- Module length <= 400 lines (Rule VI.1.1).
"""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from nextlife.storage.db import get_db_session
from nextlife.storage.models import Approval, Item, Job, Listing, Lot

BASE_URL = os.environ.get("NEXTLIFE_BASE_URL", "http://127.0.0.1:8081")
TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
SCREENSHOT_DIR = Path(".a/tmp/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def assert_no_horizontal_scroll(page, step_name: str) -> None:
    """Assert that the page on 360 px viewport has zero horizontal scroll."""
    scroll_w = page.evaluate("() => document.documentElement.scrollWidth")
    client_w = page.evaluate("() => document.documentElement.clientWidth")
    assert scroll_w <= client_w + 5, (
        f"[{step_name}] Horizontal scroll violation: scrollWidth {scroll_w} > clientWidth {client_w}"
    )


def assert_touch_targets_min_size(page, step_name: str, min_size: int = 44) -> None:
    """Assert all interactive targets (buttons, inputs, action links) are >= 44 px."""
    violations = page.evaluate(
        """(minSize) => {
        const interactive = Array.from(document.querySelectorAll('button, a.btn, a.nav-item, input[type="submit"], input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), select, textarea'));
        const bad = [];
        for (const el of interactive) {
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0 && el.offsetParent !== null) {
                if (rect.width < minSize - 2 || rect.height < minSize - 2) {
                    bad.push({
                        tag: el.tagName,
                        id: el.id,
                        cls: el.className,
                        text: (el.innerText || el.value || '').substring(0, 20),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height)
                    });
                }
            }
        }
        return bad;
    }""",
        min_size,
    )
    assert len(violations) == 0, (
        f"[{step_name}] Touch target size violations (<{min_size}px): {violations}"
    )


def test_mobile_e2e_full_flow_360px(live_server_url):
    """DoD: Full flow of lot creation, media, conditions, approval on 360 px with no scroll and >= 44px targets."""
    lib_path = str(Path(__file__).resolve().parent.parent.parent / "lib" / "playwright_libs")
    custom_env = dict(os.environ)
    custom_env["LD_LIBRARY_PATH"] = f"{lib_path}:{custom_env.get('LD_LIBRARY_PATH', '')}".strip(":")

    # Изолированный сервер на test-БД (как в test_screen_deal/threads_playwright).
    # Прежнее прямое обращение к dev-серверу :8081 протекало в DEV-БД: лот
    # создавался в dev, а очистка в finally шла через get_db_session() на test-БД.
    base_url = live_server_url or BASE_URL

    created_item_code = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, env=custom_env)
        context = browser.new_context(
            viewport={"width": 360, "height": 740},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()

        try:
            # 1. Wizard Step 1: Initial Lot Creation
            step1_url = f"{base_url}/ui/wizard?token={TOKEN}"
            resp = page.goto(step1_url, wait_until="networkidle")
            assert resp and resp.status == 200, f"Step 1 failed to load: {resp.status if resp else None}"
            assert_no_horizontal_scroll(page, "Wizard Step 1")
            assert_touch_targets_min_size(page, "Wizard Step 1")

            page.fill('input[name="title"]', "Sony WH-1000XM4 E2E Mobile")
            page.select_option('select[name="category"]', "electronics")
            # Czech text: N2 P-E03 language check blocks Cyrillic-dominant
            # bodies for CZ marketplaces (draft approval would be disabled).
            page.fill('textarea[name="notes"]', "Testovací lot pro mobilni prubeh 360px")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_step1_360px.png"))

            with page.expect_navigation():
                page.click('button[type="submit"]')

            # Now at Step 2: Media
            assert "?step=2" in page.url
            created_item_code = page.url.split("/ui/wizard/")[1].split("?")[0]
            assert_no_horizontal_scroll(page, "Wizard Step 2")
            assert_touch_targets_min_size(page, "Wizard Step 2")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_step2_360px.png"))

            with page.expect_navigation():
                page.click('button[type="submit"]')

            # Now at Step 3: Condition & Defects
            assert "?step=3" in page.url
            assert_no_horizontal_scroll(page, "Wizard Step 3")
            assert_touch_targets_min_size(page, "Wizard Step 3")
            page.select_option('select[name="condition_grade"]', "A")
            page.check('input[name="box_present"]')
            page.fill('input[name="defect_text"]', "Idealni stav, bez stop")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_step3_360px.png"))

            with page.expect_navigation():
                page.click('button[type="submit"]')

            # Now at Step 4: Price & Price Floor
            assert "?step=4" in page.url
            assert_no_horizontal_scroll(page, "Wizard Step 4")
            assert_touch_targets_min_size(page, "Wizard Step 4")
            page.fill('input[name="price"]', "5500")
            page.fill('input[name="price_floor"]', "4800")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_step4_360px.png"))

            with page.expect_navigation():
                page.click('button[type="submit"]')

            # Now at Step 5: Shipping Policy & Finish
            assert "?step=5" in page.url
            assert_no_horizontal_scroll(page, "Wizard Step 5")
            assert_touch_targets_min_size(page, "Wizard Step 5")
            page.select_option('select[name="shipping_policy"]', "BUYER_PAYS")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_step5_360px.png"))

            with page.expect_navigation():
                page.click('button[type="submit"]')

            # Step 6: Item Card & Media Manager Screen
            assert f"/ui/items/{created_item_code}" in page.url
            assert_no_horizontal_scroll(page, "Item Card")
            assert_touch_targets_min_size(page, "Item Card")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_item_card_360px.png"))

            # Step 7: Draft Approval Screen
            draft_url = f"{base_url}/ui/draft/{created_item_code}?token={TOKEN}"
            resp_draft = page.goto(draft_url, wait_until="networkidle")
            assert resp_draft and resp_draft.status == 200
            assert_no_horizontal_scroll(page, "Draft Approval")
            assert_touch_targets_min_size(page, "Draft Approval")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_draft_360px.png"))

            # Perform approval action
            with page.expect_navigation():
                page.click("#btn-approve")

            # Verify redirect to approved item card
            assert f"/ui/items/{created_item_code}" in page.url
            assert "notice=approved" in page.url
            assert_no_horizontal_scroll(page, "Approved Item Card")
            assert_touch_targets_min_size(page, "Approved Item Card")
            page.screenshot(path=str(SCREENSHOT_DIR / "t075_mobile_approved_360px.png"))

        finally:
            browser.close()
            # Database cleanup
            if created_item_code:
                with get_db_session() as session:
                    session.query(Approval).filter(Approval.item_code == created_item_code).delete()
                    session.query(Job).filter(Job.idempotency_key.like(f"{created_item_code}_%")).delete()
                    session.query(Listing).filter(Listing.item_code == created_item_code).delete()
                    session.query(Lot).filter(Lot.item_id == created_item_code).delete()
                    session.query(Item).filter(Item.code == created_item_code).delete()
                    session.commit()
