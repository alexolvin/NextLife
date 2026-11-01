"""DIRECT web publish via Playwright (Bazoš).

Bazoš publish is DIRECT (web) with SMS confirmation: the owner's phone number
is used for the listing-confirmation SMS, and the code is entered by the owner
via Telegram (NextLife never intercepts SMS). The automation runs as the
``nextlife`` user (the server), reads the phone from ``settings.json``, and
keeps a persistent Playwright profile under the data dir so the owner's session
survives across runs.

Aukro publish is NOT here — it is the FEED channel (XML feed pulled by Aukro's
Manažer prodeje), see the Aukro connector.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nextlife.core.config import get_data_dir
from nextlife.services.llm_filter import get_secrets

# Headless Chrome flags for a locked-down systemd unit (ProtectSystem=strict):
# no sandbox (no CAP_SYS_ADMIN), /dev/shm is small, no GPU.
_CHROME_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


def _profile_dir(marketplace: str) -> Path:
    d = get_data_dir() / "browser-profiles" / marketplace
    d.mkdir(parents=True, exist_ok=True)
    return d


def _recon_dir() -> Path:
    d = get_data_dir() / "recon"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _shot(page: Any, recon: Path, name: str) -> str:
    """Screenshot the page to recon/<name>.png; returns the path (best effort)."""
    path = recon / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        pass
    return str(path)


def recon_bazos() -> dict[str, Any]:
    """V0.6 recon: navigate to the Bazoš login page and dump its form (read-only).

    Discovers the login form structure with a screenshot and a form-by-form
    input dump so the publish flow can be built. Never logs in, never submits.
    """
    from playwright.sync_api import sync_playwright

    secrets = get_secrets()
    phone = (secrets.get("BAZOS_PHONE") or "").strip()
    profile = _profile_dir("bazos_cz")
    recon = _recon_dir()
    steps: list[str] = []
    errors: list[str] = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=_CHROME_ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30000)
        try:
            # 1. Go straight to the login page (moje-inzeraty.php)
            page.goto("https://www.bazos.cz/moje-inzeraty.php", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            _shot(page, recon, "bazos-2-login")
            steps.append(f"1. login page: {page.url} (phone set={bool(phone)})")

            # 2. Dump each <form> on the page with its action + inputs
            for frame in page.frames:
                if "bazos.cz" not in frame.url:
                    continue
                for i, form in enumerate(frame.query_selector_all("form")):
                    action = form.get_attribute("action") or ""
                    method = form.get_attribute("method") or ""
                    inputs = []
                    for inp in form.query_selector_all("input, textarea, select"):
                        tag = inp.evaluate("el => el.tagName")
                        typ = inp.get_attribute("type") or ""
                        name = inp.get_attribute("name") or ""
                        inputs.append(f"{tag}:{typ}:{name}")
                    steps.append(f"2. form[{i}] action={action[:50]} method={method} inputs={inputs}")

        except Exception as exc:  # noqa: BLE001 - recon must report, not crash
            errors.append(f"{type(exc).__name__}: {exc}")
            try:
                _shot(page, recon, "bazos-error")
            except Exception:
                pass
        finally:
            ctx.close()

    return {
        "ok": not errors,
        "steps": steps,
        "errors": errors,
        "recon_dir": str(recon),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def recon_bazos_publish() -> dict[str, Any]:
    """V0.6 recon: navigate the Bazoš publish flow and dump the listing form (read-only).

    Discovers the listing-form structure with screenshots and a field dump so
    the publish flow can be built. Never logs in, never submits a listing.
    Flow: home -> "Přidat inzerát" -> pick a category -> capture the form.
    """
    from playwright.sync_api import sync_playwright

    profile = _profile_dir("bazos_cz")
    recon = _recon_dir()
    steps: list[str] = []
    errors: list[str] = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=_CHROME_ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30000)
        try:
            # 1. "Přidat inzerát" (Add ad) -> category grid
            page.goto("https://www.bazos.cz/pridat-inzerat.php", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            _shot(page, recon, "bazos-pub-1-category-grid")
            steps.append(f"1. category grid: {page.url}")

            # 2. Pick a category (PC) -> see where it leads
            pc_link = page.query_selector("a:has-text('PC'), a[href*='pc']")
            if pc_link:
                pc_link.click()
                page.wait_for_timeout(5000)
            else:
                steps.append("2. PC category link not found")
            _shot(page, recon, "bazos-pub-2-after-pc")
            steps.append(f"2. after PC click: {page.url}")

            # 3. Drill into a subcategory (Notebooky) -> capture the listing form
            page.goto("https://pc.bazos.cz/notebook/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            _shot(page, recon, "bazos-pub-3-subcategory")
            steps.append(f"3. subcategory: {page.url}")

            # 4. Dump all links on the subcategory page (find the "Nabídnout"/listing-form entry)
            for frame in page.frames:
                if "bazos.cz" not in frame.url:
                    continue
                for a in frame.query_selector_all("a"):
                    href = a.get_attribute("href") or ""
                    text = (a.inner_text() or "").strip().replace("\n", " ")[:40]
                    if any(k in (href + text).lower() for k in ("nabid", "vyplnit", "form", "vytvorit", "post", "add", "priplat")):
                        steps.append(f"   LINK href={href[:60]} text={text}")
        except Exception as exc:  # noqa: BLE001 - recon must report, not crash
            errors.append(f"{type(exc).__name__}: {exc}")
            try:
                _shot(page, recon, "bazos-pub-error")
            except Exception:
                pass
        finally:
            ctx.close()

    return {
        "ok": not errors,
        "steps": steps,
        "errors": errors,
        "recon_dir": str(recon),
        "timestamp": datetime.now(UTC).isoformat(),
    }
