#!/usr/bin/env python3
"""Capture visual regression snapshots and DOM metrics across all NextLife UI screens.

Usage:
    python3 scripts/ui_snapshots.py [before|after]
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from nextlife.core.secrets import resolve_secret

ROOT_DIR = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("NEXTLIFE_BASE_URL", "http://127.0.0.1:8081")

VIEWPORTS = [
    {
        "name": "390",
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 2,
        "is_mobile": True,
        "has_touch": True,
    },
    {
        "name": "1366",
        "viewport": {"width": 1366, "height": 768},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    },
]


def discover_dynamic_routes(cookies: list[dict[str, str]]) -> dict[str, str]:
    """Find available dynamic routes (items, listings, threads, batches)."""
    routes: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

        # 1. Stock item
        try:
            page.goto(f"{BASE_URL}/ui/stock", wait_until="domcontentloaded")
            links = page.eval_on_selector_all(
                "a[href*='/ui/stock/']", "els => els.map(e => e.getAttribute('href'))"
            )
            for link in links:
                if link and link != "/ui/stock/new" and "/ui/stock/" in link:
                    routes["stock_item"] = link.split("#")[0]
                    break
        except Exception:
            pass

        # 2. Listing detail
        try:
            page.goto(f"{BASE_URL}/ui/listings?filter=all", wait_until="domcontentloaded")
            links = page.eval_on_selector_all(
                "a[href*='/ui/listings/']", "els => els.map(e => e.getAttribute('href'))"
            )
            for link in links:
                if link and "/ui/listings/" in link:
                    # ignore action URLs
                    parts = link.split("/")
                    if len(parts) >= 4 and not parts[-1].startswith(("recheck", "publish")):
                        routes["listing_detail"] = link.split("?")[0]
                        break
            if "listing_detail" not in routes:
                # check errors filter
                page.goto(f"{BASE_URL}/ui/listings?filter=errors", wait_until="domcontentloaded")
                links = page.eval_on_selector_all(
                    "a[href*='/ui/listings/']", "els => els.map(e => e.getAttribute('href'))"
                )
                for link in links:
                    if link and "/ui/listings/" in link:
                        routes["listing_detail"] = link.split("?")[0]
                        break
        except Exception:
            pass

        # 3. Message thread detail
        try:
            page.goto(f"{BASE_URL}/ui/messages", wait_until="domcontentloaded")
            links = page.eval_on_selector_all(
                "a[href*='/ui/messages/']", "els => els.map(e => e.getAttribute('href'))"
            )
            for link in links:
                if link and link != "/ui/messages":
                    routes["message_detail"] = link.split("?")[0]
                    break
        except Exception:
            pass

        # 4. Draft
        if "stock_item" in routes:
            code = routes["stock_item"].split("/")[-1]
            routes["draft"] = f"/ui/draft/{code}"

        # 5. Deals & Buy kit
        try:
            page.goto(f"{BASE_URL}/ui/buy", wait_until="domcontentloaded")
            links = page.eval_on_selector_all(
                "a[href*='/ui/buy/kits/']", "els => els.map(e => e.getAttribute('href'))"
            )
            if links:
                routes["buy_kit"] = links[0]
        except Exception:
            pass

        browser.close()
    return routes


def collect_page_metrics(page: Any) -> dict[str, Any]:
    """Extract DOM metrics required by §6.1."""
    eval_script = """() => {
        const root = document.documentElement;
        const overflow_x = root.scrollWidth - root.clientWidth;

        // Form elements names and actions
        const names = Array.from(new Set(
            Array.from(document.querySelectorAll('input, select, textarea, button'))
                .map(el => el.getAttribute('name'))
                .filter(Boolean)
        )).sort();

        const actions = Array.from(new Set(
            Array.from(document.querySelectorAll('form'))
                .map(f => f.getAttribute('action'))
                .filter(Boolean)
        )).sort();

        // Element IDs
        const ids = Array.from(new Set(
            Array.from(document.querySelectorAll('[id]'))
                .map(el => el.id)
                .filter(Boolean)
        )).sort();

        // Data attributes
        const data_attrs = [];
        for (const el of document.querySelectorAll('*')) {
            for (const attr of el.attributes) {
                if (attr.name.startsWith('data-')) {
                    data_attrs.push(attr.name);
                }
            }
        }
        const sorted_data = Array.from(new Set(data_attrs)).sort();

        // Element heights & widths for interactive elements
        const element_sizes = [];
        for (const el of document.querySelectorAll('button, input:not([type="hidden"]), select, [role="tab"]')) {
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                element_sizes.push({
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    name: el.getAttribute('name') || '',
                    id: el.id || '',
                    text: (el.innerText || el.value || '').trim().slice(0, 30),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    top: Math.round(rect.top),
                    is_fab: el.classList.contains('fab') || el.id === 'fab-add',
                });
            }
        }

        // First item top offset
        let first_item_top = null;
        const item_candidates = document.querySelectorAll('.card, .stock-card, [data-testid^="stock-card"], [data-testid^="thread-row"], [data-testid^="attention-card"], .attention-card, tr[data-testid^="row-mp-"]');
        for (const cand of item_candidates) {
            const r = cand.getBoundingClientRect();
            if (r.height > 0 && r.top > 40) {
                first_item_top = Math.round(r.top);
                break;
            }
        }

        // Visible text content for raw code scanning
        const visible_text = document.body ? document.body.innerText : '';

        return {
            overflow_x,
            form_names: names,
            form_actions: actions,
            element_ids: ids,
            data_attrs: sorted_data,
            element_sizes,
            first_item_top,
            visible_text: visible_text.slice(0, 15000)
        };
    }"""
    return page.evaluate(eval_script)


def run_snapshots(mode: str = "before") -> None:
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    out_dir = ROOT_DIR / "docs" / "ui" / mode
    out_dir.mkdir(parents=True, exist_ok=True)

    cookies = [
        {
            "name": "nextlife_token",
            "value": token,
            "domain": "127.0.0.1",
            "path": "/",
        }
    ]

    print(f"Discovering dynamic routes on {BASE_URL}...")
    dynamic_routes = discover_dynamic_routes(cookies)
    print("Discovered routes:", dynamic_routes)

    # 16 distinct functional screens (+ settings sub-tabs)
    screens: list[tuple[str, str | None]] = [
        ("stock", "/ui/stock"),
        ("stock_item", dynamic_routes.get("stock_item")),
        ("stock_new", "/ui/stock/new"),
        ("listings", "/ui/listings"),
        ("listing_detail", dynamic_routes.get("listing_detail")),
        ("attention", "/ui/attention"),
        ("settings_profile", "/ui/settings?tab=profile"),
        ("settings_marketplaces", "/ui/settings?tab=marketplaces"),
        ("settings_marketplaces_open", "/ui/settings?tab=marketplaces"),
        ("settings_ai", "/ui/settings?tab=ai"),
        ("settings_notion", "/ui/settings?tab=notion"),
        ("messages", "/ui/messages"),
        ("message_detail", dynamic_routes.get("message_detail")),
        ("buy", "/ui/buy"),
        ("buy_kit", dynamic_routes.get("buy_kit")),
        ("connectors", "/ui/connectors"),
        ("connect", "/ui/connect"),
        ("photo_stand", "/ui/photo-stand"),
        ("draft", dynamic_routes.get("draft")),
        ("deal", dynamic_routes.get("deal")),
        ("job_batch", dynamic_routes.get("job_batch")),
        ("login", "/ui/login"),
    ]

    metrics: dict[str, Any] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()

        for vp in VIEWPORTS:
            vp_name = vp["name"]
            print(f"\n--- Viewport: {vp_name}px ---")

            for screen_name, route in screens:
                key = f"{screen_name}_{vp_name}"
                if not route:
                    print(f"[{screen_name}] SKIPPED: нет данных")
                    metrics[key] = {
                        "screen": screen_name,
                        "viewport": vp_name,
                        "status": "SKIPPED: нет данных",
                        "route": None,
                    }
                    continue

                # Create isolated browser context for each viewport/login state
                context = browser.new_context(
                    viewport=vp["viewport"],
                    device_scale_factor=vp["device_scale_factor"],
                    is_mobile=vp["is_mobile"],
                    has_touch=vp["has_touch"],
                )

                if screen_name != "login":
                    context.add_cookies(cookies)

                page = context.new_page()
                console_errors: list[str] = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda err: console_errors.append(str(err)))

                target_url = f"{BASE_URL}{route}"
                try:
                    page.goto(target_url, wait_until="networkidle", timeout=15000)
                except Exception:
                    try:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                    except Exception as exc:
                        print(f"[{screen_name}] Error loading {route}: {exc}")
                        metrics[key] = {
                            "screen": screen_name,
                            "viewport": vp_name,
                            "status": f"ERROR: {exc}",
                            "route": route,
                        }
                        context.close()
                        continue

                # For settings_marketplaces_open, ensure at least one details is opened
                if screen_name == "settings_marketplaces_open":
                    page.evaluate("""() => {
                        const d = document.querySelector('details');
                        if (d) d.setAttribute('open', '');
                    }""")
                    page.wait_for_timeout(300)

                # Collect metrics
                m = collect_page_metrics(page)
                m["console_errors"] = console_errors
                m["screen"] = screen_name
                m["viewport"] = vp_name
                m["route"] = route
                m["status"] = "OK"

                # Screenshot
                screenshot_path = out_dir / f"{screen_name}_{vp_name}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)

                metrics[key] = m
                print(f"[{screen_name}] -> {screenshot_path.name} (overflow_x={m['overflow_x']}, errs={len(console_errors)})")
                context.close()

        browser.close()

    metrics_path = out_dir / "metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    print(f"\nSaved metrics: {metrics_path}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "before"
    run_snapshots(mode)
