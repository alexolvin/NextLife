
import os
from playwright.sync_api import sync_playwright

token = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
base_url = os.environ.get("NEXTLIFE_BASE_URL", "http://127.0.0.1:8081")

def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Login
        print("Logging in...")
        page.goto(f"{base_url}/ui/login?next=/ui/items")
        page.fill("input[name='token']", token)
        page.click("button[type='submit']")
        page.wait_for_url(f"**{base_url}/ui/items**")
        print("Login successful.")

        # 2. Check Item Card lightbox
        print("Checking Item Card (777)...")
        page.goto(f"{base_url}/ui/items/777?token={token}")
        images = page.locator("img[onclick*='Lightbox']").all()
        assert len(images) > 0, "No lightbox images found on item card"

        # Test clicking the first image
        images[0].click()
        lightbox = page.locator("#lightbox").first
        assert lightbox.is_visible(), "Lightbox did not appear"
        page.locator("#lightbox-close").first.click()

        # 3. Check Draft Approval and Edit Text
        print("Checking Draft Approval...")
        page.goto(f"{base_url}/ui/draft/777?token={token}")
        textarea = page.locator("textarea[name='edited_listing_text']")
        assert textarea.is_visible(), "Editable textarea for draft not found"

        # Edit text and submit
        textarea.fill("Edited Text with Playwright")
        page.click("button#btn-approve")
        page.wait_for_url(f"**{base_url}/ui/items/777?notice=approved**")

        # Verify text was updated in backend (by checking UI)
        page.goto(f"{base_url}/ui/items/777?token={token}")
        content = page.content()
        assert "Edited Text with Playwright" in content, "Edited text was not saved properly"

        # 4. Check Inbox and Reply
        print("Checking Inbox...")
        page.goto(f"{base_url}/ui/inbox?token={token}")
        textareas = page.locator("textarea[name='message']").all()
        assert len(textareas) > 0, "Reply textarea not found in inbox"

        # Send reply
        textareas[0].fill("Playwright test reply")
        page.locator("button[type='submit']").first.click()
        page.wait_for_url(f"**{base_url}/ui/inbox?notice=sent**")
        print("Inbox reply sent.")

        browser.close()
        print("All UI/UX tests passed successfully.")

if __name__ == "__main__":
    run_tests()
