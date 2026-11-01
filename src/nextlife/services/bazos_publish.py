"""Bazoš DIRECT publish (TZ M1 A.1, A.5 A1).

Bazoš has no password login: the owner's phone is verified by an SMS code.
The code is human-in-the-loop — NextLife never intercepts SMS. The owner
receives the SMS, reads the code, and sends it back via Telegram (OpenClaw
pushes it to ``POST /api/oc/inbox``). NextLife polls that inbox for the code.

The browser session is a persistent Playwright profile under the data dir so
the phone verification survives across runs. Headless Chrome flags match the
locked-down systemd unit (ProtectSystem=strict): no sandbox, no /dev/shm, no GPU.
"""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nextlife.core.config import get_data_dir
from nextlife.services.llm_filter import get_secrets

log = logging.getLogger("nextlife.bazos_publish")

_CHROME_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
_PROFILE_KEY = "bazos_cz"

# How long (seconds) the login/publish flow waits for the owner's SMS code.
SMS_WAIT_SECONDS = 600
# Poll interval (seconds) for the inbox while waiting for the SMS code.
SMS_POLL_INTERVAL = 5


def _clear_stale_singleton_lock(profile: Any) -> None:
    """Remove a stale Chrome ``Singleton*`` lock left by a crashed session.

    The publish flow is single-user and human-in-the-loop, and each flow closes
    its context before finishing — so a ``SingletonLock`` whose owning process is
    no longer running is stale (left by a crash or a service restart) and safe to
    remove. Without this, a crashed session blocks the next publish with
    "Failed to create a ProcessSingleton for your profile directory".
    """
    import os

    lock = profile / "SingletonLock"
    if not (lock.exists() or lock.is_symlink()):
        return
    # ``SingletonLock`` is a symlink to "<hostname>-<pid>". If that PID is still
    # alive, the profile is genuinely in use — leave the lock alone.
    pid = None
    try:
        target = os.readlink(lock)
        pid = int(target.rsplit("-", 1)[-1])
    except (OSError, ValueError):
        pid = None
    if pid is not None:
        try:
            os.kill(pid, 0)  # signal 0: liveness check only
            return  # process alive — live lock, do not remove
        except ProcessLookupError:
            pass  # process gone — stale
        except PermissionError:
            return  # process alive (other user) — do not remove
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = profile / name
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass


def _profile_dir() -> Any:
    d = get_data_dir() / "browser-profiles" / _PROFILE_KEY
    d.mkdir(parents=True, exist_ok=True)
    _clear_stale_singleton_lock(d)
    return d


def _recon_dir() -> Any:
    d = get_data_dir() / "recon"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_category(category: str) -> tuple[str, str]:
    """Split a Bazoš category path ``top/sub`` into ``(top, sub)``.

    ``top`` is the section key (subdomain), lowercased. ``sub`` keeps the rubric
    text so it can be matched against the form dropdown. A value without ``/``
    has an empty top — it is not a path through the tree.
    """
    raw = (category or "").strip()
    if "/" not in raw:
        return "", raw
    top, sub = raw.split("/", 1)
    return top.strip().lower(), sub.strip()


def _fold(value: str) -> str:
    """Compare category text ignoring case, diacritics and separators."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def _score_text(needle: str, candidate: str) -> int:
    left = _fold(needle)
    right = _fold(candidate)
    if not left or not right:
        return 0
    if left == right:
        return 3
    if right.startswith(left) or left.startswith(right):
        return 2
    if left in right or right in left:
        return 1
    return 0


def match_category_option(
    options: list[tuple[str, str]], needle: str
) -> tuple[tuple[str, str] | None, list[str]]:
    """Pick one dropdown option for ``needle``.

    Returns ``((value, label), [])`` for a unique best match. Returns
    ``(None, competing_labels)`` when nothing matches or several options tie,
    so the caller does not publish into an arbitrary rubric.
    Options with an empty value (the placeholder) are ignored.
    """
    scored: list[tuple[int, str, str]] = []
    for value, label in options:
        if not (value or "").strip():
            continue
        score = max(_score_text(needle, label), _score_text(needle, value))
        if score:
            scored.append((score, value, label))
    if not scored:
        return None, []
    best = max(item[0] for item in scored)
    winners = [(value, label) for score, value, label in scored if score == best]
    if len(winners) != 1:
        return None, [label for _, label in winners]
    return winners[0], []


def _category_tree() -> list[dict[str, Any]]:
    from nextlife.core.config import load_config

    mp = load_config().marketplaces[_PROFILE_KEY]
    return list(mp.category_tree or [])


def _category_tops() -> set[str]:
    return {str(node.get("top") or "").strip().lower() for node in _category_tree()} - {""}


def _require_top(top: str | None) -> str:
    """Section key safe to substitute into a Bazoš host. Raises ``ValueError``."""
    candidate = (top or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]+", candidate) or candidate not in _category_tops():
        raise ValueError(f"unknown Bazoš category section: {top!r}")
    return candidate


def _rubric_hits(category: str) -> list[tuple[str, str]]:
    """Sections whose rubric label folds equal to ``category``. Exact, not prefix."""
    needle = _fold(category)
    if not needle or "/" in (category or ""):
        return []
    hits: list[tuple[str, str]] = []
    for node in _category_tree():
        section = str(node.get("top") or "").strip().lower()
        for item in node.get("subs") or []:
            label = str(item)
            if _fold(label) == needle:
                hits.append((section, label))
    return hits


def resolve_category_path(category: str) -> str | None:
    """``top/sub`` for a stored category.

    A bare rubric is accepted when it belongs to exactly one section, so an
    approved frame saved before paths existed (``Grafické karty``) still
    publishes as ``pc/Grafické karty``. Ambiguous rubrics (``Ostatní``) are
    not guessed.
    """
    raw = (category or "").strip()
    top, sub = parse_category(raw)
    if top and sub and re.fullmatch(r"[a-z0-9]+", top) and top in _category_tops():
        return f"{top}/{sub}"
    hits = _rubric_hits(raw)
    if len(hits) == 1:
        return f"{hits[0][0]}/{hits[0][1]}"
    return None


def category_path_error(category: str) -> str | None:
    """Russian error when ``category`` is not a known ``top/sub`` path.

    Does not check the live dropdown — that list is read from the form.
    ``None`` means the section is known and a subcategory text is present,
    including a bare rubric that resolves to one section.
    """
    if resolve_category_path(category):
        return None
    top, sub = parse_category(category)
    if not top or not sub:
        shown = category.strip() if category and category.strip() else "—"
        hits = _rubric_hits(shown)
        if len(hits) > 1:
            sections = ", ".join(sorted({section for section, _ in hits}))
            return (
                f"Рубрика «{shown}» есть в нескольких разделах ({sections}). "
                "Устранение: укажите путь «раздел/подкатегория», например «pc/Notebooky»."
            )
        return (
            f"Категория «{shown}» не в формате «раздел/подкатегория» "
            "(например «pc/Notebooky»). "
            "Устранение: на шаге 3 мастера заново сформируйте объявление для Bazoš "
            "или впишите путь из дерева категорий."
        )
    if not re.fullmatch(r"[a-z0-9]+", top) or top not in _category_tops():
        known = ", ".join(sorted(_category_tops())) or "(дерево категорий пусто)"
        return (
            f"Раздела «{top}» нет в дереве категорий Bazoš. "
            f"Устранение: укажите один из разделов: {known}."
        )
    return None


def _publish_urls(top: str | None = None) -> dict[str, str]:
    """Bazoš publish URLs from the marketplace config (routes.publish_*).

    ``publish_login`` is a path joined to the base URL and does not need a
    section. Form and recon templates contain ``{top}`` — the category
    subdomain — and are filled only when ``top`` is a known section key.
    Without ``top`` those three values are empty, so a caller cannot navigate
    to a literal ``{top}`` host.
    """
    from nextlife.core.config import load_config

    mp = load_config().marketplaces[_PROFILE_KEY]
    routes = mp.routes
    base = mp.base_url.rstrip("/")
    login = base + str(routes.get("publish_login", "/moje-inzeraty.php"))
    if top is None:
        return {"login": login, "form": "", "recon": "", "recon_direct": ""}
    section = _require_top(top)

    def fill(key: str) -> str:
        return str(routes.get(key, "")).replace("{top}", section)

    return {
        "login": login,
        "form": fill("publish_form"),
        "recon": fill("publish_recon"),
        "recon_direct": fill("publish_recon_direct"),
    }


def _shot(page: Any, name: str) -> str:
    path = _recon_dir() / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        pass
    return str(path)


def _extract_sms_code(text: str) -> str:
    """Pull a 4-6 digit code out of the owner's Telegram message."""
    m = re.search(r"\b(\d{4,6})\b", text or "")
    return m.group(1) if m else ""


def _poll_inbox_for_code(timeout_s: int = SMS_WAIT_SECONDS) -> dict[str, Any]:
    """Poll the OpenClaw inbox for the owner's SMS code.

    Reads the pending inbox (messages from the owner via Telegram) and
    extracts a numeric code. Returns {"ok": bool, "code": str, "raw": str}.
    """
    from nextlife.comms.inbox import get_pending_inbox
    from nextlife.storage.db import get_db_session

    deadline = time.monotonic() + timeout_s
    last_raw = ""
    while time.monotonic() < deadline:
        try:
            with get_db_session() as session:
                msgs = get_pending_inbox(session)
            for msg in msgs:
                raw = msg.get("text_original") or ""
                last_raw = raw
                code = _extract_sms_code(raw)
                if code:
                    return {"ok": True, "code": code, "raw": raw}
        except Exception as exc:  # noqa: BLE001 - keep polling on transient errors
            log.warning("bazos_publish: inbox poll error: %s", exc)
        time.sleep(SMS_POLL_INTERVAL)
    return {"ok": False, "code": "", "raw": last_raw}


def _notify_owner(body: str, idempotency_key: str) -> None:
    """Enqueue a Telegram outbox message to the owner (OpenClaw delivers it)."""
    from nextlife.comms.outbox import enqueue_outbox
    from nextlife.storage.db import get_db_session

    try:
        with get_db_session() as session:
            enqueue_outbox(
                session=session,
                transport="TELEGRAM",
                body=body,
                idempotency_key=idempotency_key,
                recipient_hint="OWNER",
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - notify is best-effort
        log.warning("bazos_publish: notify owner failed: %s", exc)


# Module-level handle to the in-flight browser context (held open between
# login_start and login_confirm). The owner sends the SMS code in the chat;
# the agent calls login_confirm with the code to complete the login.
_bazos_pw: Any = None
_bazos_ctx: Any = None


def bazos_login_start() -> dict[str, Any]:
    """Step 1 of Bazoš login: fill the login form, submit, send the SMS.

    Launches a persistent browser, fills form[1] (email + phone), and submits
    it. Bazoš sends an SMS to the owner's phone. The browser stays open (held
    in a module-level handle) so the agent can call ``bazos_login_confirm``
    with the owner's SMS code to complete the login.
    """
    global _bazos_pw, _bazos_ctx
    from playwright.sync_api import sync_playwright

    secrets = get_secrets()
    phone = (secrets.get("BAZOS_PHONE") or "").strip()
    # Email comes from the process env (.env), not settings.json (which only
    # holds BAZOS_PHONE / SELLER_NAME / SELLER_PSC).
    email = (os.environ.get("BAZOS_CZ_ACCOUNT_EMAIL") or "").strip()
    profile = _profile_dir()
    steps: list[str] = []
    errors: list[str] = []

    if not phone:
        return {"ok": False, "steps": steps, "errors": ["BAZOS_PHONE not set"], "timestamp": datetime.now(UTC).isoformat()}

    # Close any previous in-flight context (stale from a prior attempt)
    if _bazos_ctx is not None:
        try:
            _bazos_ctx.close()
        except Exception:
            pass
        _bazos_ctx = None

    _bazos_pw = sync_playwright().start()
    _bazos_ctx = _bazos_pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
        executable_path="/usr/bin/google-chrome",
        args=_CHROME_ARGS,
    )
    page = _bazos_ctx.pages[0] if _bazos_ctx.pages else _bazos_ctx.new_page()
    page.set_default_timeout(30000)
    try:
        # 1. Navigate to the login page
        page.goto(_publish_urls()["login"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        _shot(page, "bazos-login-1-form")
        steps.append(f"1. login page: {page.url}")

        # 2. Fill + submit form[1] (email + phone) — saves the credentials
        page.fill("input[name='mail']", email)
        page.fill("input[name='telefon']", phone)
        steps.append(f"2. filled email={email} phone={phone}")
        page.click("form:has(input[name='telefon']) button[type='submit'], form:has(input[name='telefon']) input[type='submit']")
        page.wait_for_timeout(6000)
        _shot(page, "bazos-login-2-after-form1")
        steps.append(f"2. submitted form[1], now at: {page.url}")

        # 3. Fill + submit form[2] (checkbox + phone in 'teloverit') — sends the SMS
        try:
            page.check("input[name='podminky']", timeout=5000)
            steps.append("3. checked terms")
        except Exception:
            steps.append("3. no terms checkbox (skipped)")
        page.fill("input[name='teloverit']", phone)
        page.click("form:has(input[name='teloverit']) button[type='submit'], form:has(input[name='teloverit']) input[type='submit']")
        page.wait_for_timeout(6000)
        _shot(page, "bazos-login-3-after-form2")
        # Dump page HTML for diagnosis
        try:
            html = page.content()
            Path("/tmp/bazos-login-3-after-form2.html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        steps.append(f"3. submitted form[2], now at: {page.url} — SMS sent to {phone}")

    except Exception as exc:  # noqa: BLE001 - login must report, not crash
        errors.append(f"{type(exc).__name__}: {exc}")
        try:
            _shot(page, "bazos-login-error")
        except Exception:
            pass
        try:
            _bazos_ctx.close()
        except Exception:
            pass
        _bazos_ctx = None

    return {
        "ok": not errors,
        "steps": steps,
        "errors": errors,
        "awaiting_sms_code": not errors,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def bazos_login_confirm(code: str) -> dict[str, Any]:
    """Step 2 of Bazoš login: enter the owner's SMS code and complete login.

    The browser context is held open from ``bazos_login_start``. Enters the
    code in form[2] (``teloverit``), checks the terms box, and submits. The
    session is saved to the persistent browser profile.
    """
    global _bazos_ctx, _bazos_pw
    steps: list[str] = []
    errors: list[str] = []

    if _bazos_ctx is None:
        return {"ok": False, "steps": steps, "errors": ["No in-flight browser. Call login_start first."], "timestamp": datetime.now(UTC).isoformat()}

    page = _bazos_ctx.pages[0] if _bazos_ctx.pages else _bazos_ctx.new_page()
    try:
        # Dump page HTML for diagnosis (before any interaction)
        try:
            html = page.content()
            _recon_dir().joinpath("bazos-confirm-page.html").write_text(html, encoding="utf-8")
            steps.append(f"0. page: {page.url}, html {len(html)} chars")
        except Exception:
            pass

        # 1. Find the code input — try multiple selectors
        code_selector = ""
        for sel in [
            "input[name='teloverit']",
            "input[name='kod']",
            "input[type='text']:visible",
            "input[type='tel']:visible",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=3000):
                    code_selector = sel
                    break
            except Exception:
                continue
        if not code_selector:
            raise RuntimeError(f"No code input found. Page: {page.url}")
        steps.append(f"1. code input: {code_selector}")

        page.fill(code_selector, code)
        try:
            page.check("input[name='podminky']", timeout=3000)
        except Exception:
            pass
        _shot(page, "bazos-login-3-code-entered")
        # Submit the form containing the code input
        page.click(f"form:has({code_selector}) button[type='submit'], form:has({code_selector}) input[type='submit']")
        page.wait_for_timeout(6000)
        _shot(page, "bazos-login-4-logged-in")
        steps.append(f"2. submitted code, now at: {page.url}")

    except Exception as exc:  # noqa: BLE001 - login must report, not crash
        errors.append(f"{type(exc).__name__}: {exc}")
        try:
            _shot(page, "bazos-login-error")
        except Exception:
            pass
    finally:
        try:
            _bazos_ctx.close()
        except Exception:
            pass
        _bazos_ctx = None

    return {
        "ok": not errors,
        "steps": steps,
        "errors": errors,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def bazos_check_login() -> dict[str, Any]:
    """Check if the persistent browser profile has a valid Bazoš session."""
    from playwright.sync_api import sync_playwright

    profile = _profile_dir()
    steps: list[str] = []
    errors: list[str] = []

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
        executable_path="/usr/bin/google-chrome",
        args=_CHROME_ARGS,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(_publish_urls()["login"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        content = page.content()
        logged_in = "Moje inzeráty" in content or "Přidat inzerát" in content
        steps.append(f"URL: {page.url}")
        steps.append(f"logged_in: {logged_in}")
        _shot(page, "bazos-check-login")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        ctx.close()
        pw.stop()

    return {
        "ok": not errors,
        "logged_in": "logged_in: True" in steps,
        "steps": steps,
        "errors": errors,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def bazos_list_inzeraty() -> dict[str, Any]:
    """List the owner's Bazoš inzeraty from the 'moje-inzeraty' page.

    Navigates to the login page (requires a logged-in session) and returns each
    listing's title + URL. Used to verify that a publish actually created a
    listing on the marketplace — the publish flow's ``ok`` only means "no
    exception", not "listing created".
    """
    from playwright.sync_api import sync_playwright

    profile = _profile_dir()
    steps: list[str] = []
    errors: list[str] = []
    listings: list[dict[str, str]] = []

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
        executable_path="/usr/bin/google-chrome",
        args=_CHROME_ARGS,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(_publish_urls()["login"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        steps.append(f"URL: {page.url}")
        for a in page.query_selector_all("a[href*='/inzerat/']"):
            href = a.get_attribute("href") or ""
            title = (a.inner_text() or "").strip()
            if title and all(existing["url"] != href for existing in listings):
                listings.append({"title": title, "url": href})
        _shot(page, "bazos-moje-inzeraty")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        ctx.close()
        pw.stop()

    return {
        "ok": not errors,
        "listings": listings,
        "steps": steps,
        "errors": errors,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def bazos_recon_publish() -> dict[str, Any]:
    """Recon the Bazoš publish form: navigate to the listing form and dump its fields.

    Flow: "Přidat inzerát" → category grid → pick a category → subcategory → listing form.
    Dumps all form fields (name, type, label) so the publish flow can be built.
    """
    from playwright.sync_api import sync_playwright

    profile = _profile_dir()
    steps: list[str] = []
    errors: list[str] = []
    fields: list[dict[str, str]] = []

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
        executable_path="/usr/bin/google-chrome",
        args=_CHROME_ARGS,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(30000)
    try:
        # 1. Navigate to "Přidat inzerát" (PC section — this recon is PC-specific).
        page.goto(_publish_urls("pc")["form"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        _shot(page, "bazos-pub-recon-1")
        steps.append(f"1. category grid: {page.url}")

        # 2. Pick PC category
        pc_link = page.query_selector("a:has-text('PC'), a[href*='pc.']")
        if pc_link:
            pc_link.click()
            page.wait_for_timeout(5000)
            _shot(page, "bazos-pub-recon-2")
            steps.append(f"2. after PC: {page.url}")
        else:
            steps.append("2. PC link not found, trying direct URL")
            page.goto(_publish_urls("pc")["recon"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            steps.append(f"2. direct PC: {page.url}")

        # 3. On the category page, find the "create listing" link
        steps.append("3. looking for create-listing link on category page...")
        create_link = None
        for a in page.query_selector_all("a, button, input[type='submit']"):
            href = a.get_attribute("href") or ""
            text = (a.inner_text() or a.get_attribute("value") or "").strip().replace("\n", " ")[:50]
            if any(k in (href + text).lower() for k in ("vlozit", "nabid", "vyplnit", "vytvorit", "post", "pridat", "add", "new")):
                steps.append(f"   CANDIDATE href={href[:60]} text={text}")
                if not create_link:
                    create_link = a
        if create_link:
            create_link.click()
            page.wait_for_timeout(5000)
            _shot(page, "bazos-pub-recon-3-form")
            steps.append(f"3. clicked create link, now at: {page.url}")
        else:
            steps.append("3. no create-listing link found, trying /vlozit-inzerat.php")
            page.goto(_publish_urls("pc")["recon_direct"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            _shot(page, "bazos-pub-recon-3-form")
            steps.append(f"3. direct URL: {page.url}")

        # 4. Dump all forms on the current page (should be the listing form)
        forms = page.query_selector_all("form")
        steps.append(f"4. found {len(forms)} forms")
        for i, form in enumerate(forms):
            action = form.get_attribute("action") or ""
            method = form.get_attribute("method") or "get"
            inputs = form.query_selector_all("input, textarea, select")
            steps.append(f"   form[{i}] action={action} method={method} fields={len(inputs)}")
            for inp in inputs:
                name = inp.get_attribute("name") or ""
                typ = inp.get_attribute("type") or inp.evaluate("el => el.tagName.toLowerCase()")
                val = inp.get_attribute("value") or ""
                label = ""
                inp_id = inp.get_attribute("id")
                if inp_id:
                    lbl = page.query_selector(f"label[for='{inp_id}']")
                    if lbl:
                        label = (lbl.inner_text() or "").strip()[:50]
                placeholder = inp.get_attribute("placeholder") or ""
                fields.append({"name": name, "type": typ, "label": label, "placeholder": placeholder, "value": val[:60]})
                steps.append(f"     {typ} name={name} val={val[:40]} label={label[:30]}")

    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
        try:
            _shot(page, "bazos-pub-recon-error")
        except Exception:
            pass
    finally:
        ctx.close()
        pw.stop()

    return {
        "ok": not errors,
        "steps": steps,
        "errors": errors,
        "fields": fields,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _evidence_dir() -> Any:
    d = get_data_dir() / "evidence" / "bazos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bazos_publish_listing(
    *,
    title: str,
    description: str,
    price: str,
    category: str = "",
    location: str = "",
    seller_name: str = "",
    dry_run: bool = False,
    item_code: str = "",
) -> dict[str, Any]:
    """Publish a listing on Bazoš via the web form.

    Opens the persistent browser profile (must be logged in), navigates to
    the category's ``pridat-inzerat.php``, fills the listing form, and submits.
    With ``dry_run=True`` the form is filled but NOT submitted.
    Returns the result with the new listing URL on success.

    ТЗ «Склад» §2.2: after submit, the final page URL and its HTML are saved to
    ``<data_dir>/evidence/bazos/<item_code>_<timestamp>.html``; the file path and
    URL are returned in ``evidence_path`` / ``final_url`` for ``listing.publish_evidence``.
    """
    from playwright.sync_api import sync_playwright

    from nextlife.services.llm_filter import get_secrets

    secrets = get_secrets()
    phone = (secrets.get("BAZOS_PHONE") or "").strip()
    psc = (secrets.get("SELLER_PSC") or "").strip()
    seller = (secrets.get("SELLER_NAME") or "").strip() or seller_name
    heslo = (secrets.get("BAZOS_PASSWORD") or "").strip()
    profile = _profile_dir()
    steps: list[str] = []
    errors: list[str] = []
    evidence_path: str = ""
    final_url: str = ""

    # The section decides the host. Refuse before opening the browser so a
    # category that is only a rubric name cannot be published on the wrong one.
    path_error = category_path_error(category)
    if path_error:
        return {
            "ok": False,
            "steps": steps,
            "errors": [path_error],
            "evidence_path": evidence_path,
            "final_url": final_url,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    top, sub = parse_category(resolve_category_path(category) or category)

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
        executable_path="/usr/bin/google-chrome",
        args=_CHROME_ARGS,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(30000)
    try:
        # 1. Navigate to this section's publish form (top = subdomain).
        url = _publish_urls(top)["form"]
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        _shot(page, "bazos-pub-1-form")
        steps.append(f"1. form page: {page.url}")

        # 2. Fill the listing form. The form's `action` attribute is set by
        # JavaScript (absent at DOMContentLoaded), so select it by a stable
        # field (the title input) rather than `form[action='/insert.php']`.
        form = page.query_selector("form:has(input[name='nadpis'])")
        if not form:
            # The listing form is absent — the session likely expired and the
            # page is showing the phone-verification form instead. Re-authenticate
            # via the SMS login flow (bazos_login_start / bazos_login_confirm).
            raise RuntimeError(
                "Сессия Bazoš истекла (форма объявления не найдена). "
                "Устранение: попросите агента выполнить SMS-логин (bazos_login_start → код из SMS)."
            )
        steps.append("2. found listing form")

        # Subcategory select. `sub` is the rubric (the part after top/), matched
        # against the option label or value. A miss or a tie does not submit:
        # the form default would publish into the wrong rubric.
        cat_select = form.query_selector("select[name='category']")
        category_selected = False
        if not cat_select:
            errors.append(
                "На форме Bazoš нет списка подкатегорий. "
                "Устранение: повторите SMS-логин и проверьте, что раздел категории открылся."
            )
        else:
            options = cat_select.query_selector_all("option")
            cat_labels = [
                (o.get_attribute("value") or "", (o.inner_text() or "").strip()) for o in options
            ]
            steps.append(f"2. category options: {cat_labels[:10]}")
            chosen, competing = match_category_option(cat_labels, sub)
            if chosen is None:
                visible = [lbl for _, lbl in cat_labels if lbl]
                if competing:
                    detail = (
                        f"Подкатегория «{sub}» раздела «{top}» подходит сразу к нескольким "
                        f"пунктам: {', '.join(competing)}. "
                    )
                else:
                    shown = ", ".join(visible[:40])
                    extra = f" (и ещё {len(visible) - 40})" if len(visible) > 40 else ""
                    detail = (
                        f"Подкатегории «{sub}» нет в списке раздела «{top}». "
                        f"Пункты формы: {shown}{extra}. "
                    )
                errors.append(
                    detail
                    + f"Устранение: на шаге 3 укажите «{top}/» и точное название из этого списка."
                )
            else:
                val, lbl = chosen
                form.select_option("select[name='category']", value=val)
                steps.append(f"2. category selected: {lbl} (val={val})")
                category_selected = True

        if category_selected:
            # Title
            form.fill("input[name='nadpis']", title)
            steps.append(f"2. title: {title}")

            # Description
            form.fill("textarea[name='popis']", description)
            steps.append(f"2. description: {len(description)} chars")

            # Price
            form.fill("input[name='cena']", price)
            steps.append(f"2. price: {price}")

            # Location (PSČ) — from SELLER_PSC secret, fallback to `location` param.
            lokalita = psc or location
            if lokalita:
                form.fill("input[name='lokalita']", lokalita)
                steps.append(f"2. lokalita: {lokalita}")
            else:
                steps.append(
                    "2. ⚠️ SELLER_PSC не задан — validate() заблокирует submit. "
                    "Устранение: Настройки → Данные продавца → «Почтовый индекс»."
                )

            # Phone — from BAZOS_PHONE secret.
            if phone:
                form.fill("input[name='telefoni']", phone)
                steps.append(f"2. telefoni: {phone}")
            else:
                steps.append(
                    "2. ⚠️ BAZOS_PHONE не задан — validate() заблокирует submit. "
                    "Устранение: Настройки → Bazoš → «Телефон»."
                )

            # Seller name — from SELLER_NAME secret, fallback to `seller_name` param.
            if seller:
                form.fill("input[name='jmeno']", seller)
                steps.append(f"2. jmeno: {seller}")
            else:
                steps.append(
                    "2. ⚠️ SELLER_NAME не задан — validate() заблокирует submit. "
                    "Устранение: Настройки → Данные продавца → «Имя»."
                )

            # Bazoš account password — from BAZOS_PASSWORD secret.
            if heslo:
                form.fill("input[name='heslobazar']", heslo)
                steps.append("2. heslobazar: ***")
            else:
                steps.append(
                    "2. ⚠️ BAZOS_PASSWORD не задан — validate() заблокирует submit. "
                    "Устранение: Настройки → Bazoš → «Пароль аккаунта»."
                )

            _shot(page, "bazos-pub-2-filled")
            steps.append("2. form filled")

            if dry_run:
                steps.append("3. dry_run — skipping submit")
                return {
                    "ok": True,
                    "steps": steps,
                    "errors": errors,
                    "dry_run": True,
                    "timestamp": datetime.now(UTC).isoformat(),
                }

            # 3. Submit.
            form.click("input[type='submit'], button[type='submit']")
            page.wait_for_timeout(8000)
            _shot(page, "bazos-pub-3-submitted")
            steps.append(f"3. submitted, now at: {page.url}")
            # If the click didn't navigate, try requestSubmit() (fires the submit event)
            if "pridat-inzerat" in page.url:
                try:
                    form.evaluate("el => el.requestSubmit()")
                    page.wait_for_timeout(5000)
                    steps.append(f"3. after requestSubmit: {page.url}")
                except Exception as exc:  # noqa: BLE001
                    steps.append(f"3. requestSubmit error: {exc}")

            # 4. Check for success. On success Bazoš redirects to the form's action
            # (insert.php) with "Inzerát byl vložen/změněn"; the ad becomes visible
            # in a few minutes. A created listing URL is .../inzerat/<id>-<slug>.php.
            # The form page is pridat-inzerat.php — staying there means validate()
            # blocked the submit (no listing created).
            content = page.content()
            new_url = page.url
            try:
                body_text = " ".join(page.inner_text("body").split())
            except Exception:  # noqa: BLE001
                body_text = ""
            listing_created = (
                "insert.php" in new_url
                or "inzerat/" in new_url
                or "byl vložen" in body_text
                or "confirmac" in new_url.lower()
                or "potvrzen" in new_url.lower()
            )
            if listing_created:
                steps.append(f"4. ✅ listing created: {new_url}")
            else:
                # Form was NOT submitted — still on the form page. Report a failure.
                steps.append(f"4. ❌ not submitted, still at: {new_url}")
                steps.append(f"4. page text: {body_text[:1000]}")
                errors.append("Form not submitted — no listing created (page stayed on the form)")

            # ТЗ «Склад» §2.2: save final URL + HTML to the evidence dir.
            final_url = new_url
            if item_code:
                ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                ev_path = _evidence_dir() / f"{item_code}_{ts}.html"
                ev_path.write_text(content, encoding="utf-8")
                evidence_path = str(ev_path)
                steps.append(f"5. evidence saved: {evidence_path}")

    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
        try:
            _shot(page, "bazos-pub-error")
        except Exception:
            pass
    finally:
        ctx.close()
        pw.stop()

    return {
        "ok": not errors,
        "steps": steps,
        "errors": errors,
        "evidence_path": evidence_path,
        "final_url": final_url,
        "timestamp": datetime.now(UTC).isoformat(),
    }
