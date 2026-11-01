import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from nextlife.core.exceptions import SecretResolutionError
from nextlife.core.secrets import resolve_secret

lib_path = str(Path("lib/playwright_libs").resolve())
custom_env = dict(os.environ)
custom_env["LD_LIBRARY_PATH"] = f"{lib_path}:{custom_env.get('LD_LIBRARY_PATH', '')}".strip(":")

try:
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
except SecretResolutionError:
    raise SystemExit(
        "NEXTLIFE_WEB_TOKEN не задан в окружении процесса: задайте токен "
        "веб-интерфейса (auth.web_token_secret_ref)"
    ) from None
base_url = "http://127.0.0.1:8081"
SCREENSHOT_DIR = Path(".a/tmp/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

routes_to_test = [
    ("items_feed", "/ui/items"),
    ("item_detail_777", "/ui/items/777"),
    ("item_detail_259", "/ui/items/259"),
    ("draft_777", "/ui/draft/777"),
    ("shipping_777", "/ui/lots/777/shipping"),
    ("lot_edit_777", "/ui/lots/777/edit"),
    ("dashboard", "/ui/dashboard"),
    ("archive", "/ui/archive"),
    ("threads", "/ui/threads"),
    ("preview_bazos", "/ui/items/777/preview/bazos_cz"),
    ("wizard", "/ui/wizard"),
]

results = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, env=custom_env)
    context = browser.new_context(
        viewport={"width": 360, "height": 740},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
    )
    page = context.new_page()

    # First authenticate via token
    page.goto(f"{base_url}/ui/items?token={token}", wait_until="networkidle")

    for name, path in routes_to_test:
        url = f"{base_url}{path}"
        try:
            resp = page.goto(url, wait_until="networkidle")
            status = resp.status if resp else "unknown"
            scroll_w = page.evaluate("() => document.documentElement.scrollWidth")
            client_w = page.evaluate("() => document.documentElement.clientWidth")
            overflow = scroll_w > client_w

            # Screenshot
            shot_file = SCREENSHOT_DIR / f"audit_360px_{name}.png"
            page.screenshot(path=str(shot_file), full_page=True)

            print(f"[{name}] HTTP {status}, scrollWidth={scroll_w}, clientWidth={client_w}, overflow={overflow}")
            results.append({
                "name": name,
                "path": path,
                "status": status,
                "overflow": overflow,
                "screenshot": str(shot_file),
                "error": None
            })
        except Exception as e:
            print(f"[{name}] ERROR: {e}")
            results.append({
                "name": name,
                "path": path,
                "status": "ERROR",
                "overflow": None,
                "screenshot": None,
                "error": str(e)
            })

    # Test Alpine JS interactivity on shipping page
    page.goto(f"{base_url}/ui/lots/777/shipping", wait_until="networkidle")
    # Wait for Alpine to initialize or verify options
    buyer_total_el = page.query_selector("[data-testid='buyer-total']")
    print(f"Shipping buyer_total element present: {buyer_total_el is not None}")

    # Test Lightbox opening on feed
    page.goto(f"{base_url}/ui/items", wait_until="networkidle")
    first_thumb = page.query_selector(".feed-thumb")
    if first_thumb and first_thumb.evaluate("el => el.tagName") == "IMG":
        first_thumb.click()
        lightbox = page.query_selector("#lightbox")
        print(f"Lightbox visible after click: {lightbox.is_visible() if lightbox else False}")

    browser.close()

print("\n=== AUDIT SUMMARY ===")
for r in results:
    status_icon = "✅" if r["status"] == 200 and not r["overflow"] else "❌"
    print(f"{status_icon} {r['name']} ({r['path']}): HTTP {r['status']}, overflow={r['overflow']}")
