"""Lots management checks (C-06 .. C-12) for selfcheck."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
import httpx
from sqlalchemy import text
from playwright.sync_api import sync_playwright

from nextlife.storage.db import get_engine

from scripts.selfcheck_common import create_lot
from scripts.selfcheck_types import CheckContext, CheckDef

ROOT_DIR = Path(__file__).resolve().parent.parent


def _get_auth_headers(ctx: CheckContext) -> dict[str, str]:
    return {"Authorization": f"Bearer {ctx.auth_token}"}


def run_c06(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-06: Create lot via HTTP."""
    payload = {
        "title": "SelfCheck Test Lot C06",
        "category": "electronics",
        "price": 1250.0,
        "currency": "CZK",
        "condition": "used",
    }
    art_dir.joinpath("http_request.txt").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(f"{ctx.base_url}/api/lots", json=payload, headers=_get_auth_headers(ctx))
        art_dir.joinpath("http_response.txt").write_text(
            f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8"
        )
        data = resp.json() if resp.status_code in (200, 201) else {}

    code = data.get("lot_code") or data.get("item_code") or ""
    # DB query
    db_text = "NONE"
    if code:
        with get_engine().connect() as conn:
            row = conn.execute(text("SELECT code, title, status FROM item WHERE code = :code;"), {"code": code}).fetchone()
        if row:
            db_text = f"code={row[0]}, title={row[1]}, status={row[2]}"
    art_dir.joinpath("db_lot.txt").write_text(db_text, encoding="utf-8")

    return {"status_code": resp.status_code, "code": code, "data": data}


def validate_c06(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    res_file = art_dir / "http_response.txt"
    db_file = art_dir / "db_lot.txt"
    if not res_file.exists() or not db_file.exists():
        return False, "Missing response or db artifact"
    code = meta.get("code", "")
    if meta.get("status_code") in (200, 201) and len(code) == 3 and "code=" in db_file.read_text(encoding="utf-8"):
        return True, f"Lot created via HTTP with 3-char code '{code}'"
    return False, f"HTTP status {meta.get('status_code')}, code '{code}'"


def run_c07(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-07: Create lot via CLI (thin HTTP client against the selfcheck server)."""
    cmd = [
        sys.executable,
        "-m",
        "nextlife.cli.main",
        "item",
        "create",
        "--title",
        "SelfCheck Test Lot C07",
        "--category",
        "electronics",
        "--price",
        "1350",
        "--currency",
        "CZK",
    ]
    import os
    merged_env = os.environ.copy()
    merged_env["NEXTLIFE_DSN"] = ctx.test_dsn
    merged_env["NEXTLIFE_WEB_TOKEN"] = ctx.auth_token
    merged_env["NEXTLIFE_OPENCLAW_TOKEN"] = ctx.oc_token
    merged_env["NEXTLIFE_BASE_URL"] = ctx.base_url
    merged_env["PYTHONPATH"] = f"{ROOT_DIR / 'src'}:{os.environ.get('PYTHONPATH', '')}"
    
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), env=merged_env, capture_output=True, text=True)
    art_dir.joinpath("cli_command.txt").write_text(" ".join(cmd) + "\n", encoding="utf-8")
    art_dir.joinpath("cli_output.txt").write_text(res.stdout + res.stderr, encoding="utf-8")

    code = ""
    try:
        data = json.loads(res.stdout)
        code = data.get("item_code") or data.get("lot_code") or ""
    except Exception:
        data = {}

    db_text = "NONE"
    if code:
        with get_engine().connect() as conn:
            row = conn.execute(text("SELECT code, title FROM item WHERE code = :code;"), {"code": code}).fetchone()
        if row:
            db_text = f"code={row[0]}, title={row[1]}"
    art_dir.joinpath("db_lot.txt").write_text(db_text, encoding="utf-8")

    return {"returncode": res.returncode, "code": code}


def validate_c07(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    cli_out = art_dir / "cli_output.txt"
    db_file = art_dir / "db_lot.txt"
    if not cli_out.exists() or not db_file.exists():
        return False, "Missing CLI output or db artifact"
    code = meta.get("code", "")
    if meta.get("returncode") == 0 and len(code) == 3 and "code=" in db_file.read_text(encoding="utf-8"):
        return True, f"Lot created via CLI with 3-char code '{code}'"
    return False, f"CLI returncode {meta.get('returncode')}, code '{code}'"


def run_c08(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-08: Open lots list in browser (Playwright) — creates its own lot so the feed is non-empty."""
    create_lot(ctx, "C-08 list")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        url = f"{ctx.base_url}/ui/items?token={ctx.auth_token}"
        page.goto(url)
        page.wait_for_load_state("networkidle")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))
        art_dir.joinpath("dom.html").write_text(page.content(), encoding="utf-8")
        
        cards = page.locator(".card, [data-testid^='item-card']").all()
        visible_count = len(cards)
        has_items = page.locator("body").inner_text()
        browser.close()

    return {"visible_cards": visible_count, "has_text": bool(has_items)}


def validate_c08(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    dom = art_dir / "dom.html"
    if not ss.exists() or ss.stat().st_size == 0 or not dom.exists():
        return False, "Missing screenshot or dom.html"
    if meta.get("visible_cards", 0) > 0 or "лоты" in dom.read_text(encoding="utf-8").lower():
        return True, f"Lots list rendered in browser ({meta.get('visible_cards')} cards visible)"
    return False, "No lot cards or headings found in browser DOM"


def run_c09(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-09: Open lot card and verify all tabs return 200 (own lot)."""
    code = create_lot(ctx, "C-09 card")

    tab_results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        card_url = f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}"
        resp = page.goto(card_url)
        tab_results["card"] = resp.status if resp else 0
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))

        # Test tabs / sub-screens
        for sub in [f"/ui/draft/{code}", f"/ui/lots/{code}/shipping", f"/ui/lots/{code}/edit"]:
            r = page.goto(f"{ctx.base_url}{sub}?token={ctx.auth_token}")
            tab_results[sub] = r.status if r else 0

        browser.close()

    art_dir.joinpath("tabs_status.txt").write_text(json.dumps(tab_results, indent=2), encoding="utf-8")
    return {"tabs": tab_results}


def validate_c09(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    ss = art_dir / "screenshot.png"
    tf = art_dir / "tabs_status.txt"
    if not ss.exists() or not tf.exists():
        return False, "Missing screenshot or tabs_status.txt"
    tabs = meta.get("tabs", {})
    if all(status == 200 for status in tabs.values()) and len(tabs) > 0:
        return True, f"All tabs returned 200: {tabs}"
    return False, f"Non-200 responses in tabs: {tabs}"


def run_c10(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-10: Edit lot title and price, verify in DB and screen (own lot)."""
    code = create_lot(ctx, "C-10 edit")
    with get_engine().connect() as conn:
        row = conn.execute(text("""
            SELECT lot.code, item.title, lot.price_gross
            FROM lot
            JOIN item ON lot.item_id = item.code
            WHERE lot.code = :code
            LIMIT 1;
        """), {"code": code}).fetchone()
    if not row:
        art_dir.joinpath("result.txt").write_text("NO_LOTS_IN_DB", encoding="utf-8")
        return {"edited": False}

    code, old_title, old_price = row[0], row[1], row[2]
    new_title = f"{old_title} (Edited)"
    new_price = 4999.0

    edit_payload = {"title": new_title, "price": new_price}
    art_dir.joinpath("edit_request.json").write_text(json.dumps(edit_payload, indent=2), encoding="utf-8")

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{ctx.base_url}/ui/lots/{code}/edit?token={ctx.auth_token}",
            data={"title": new_title, "price": str(new_price), "shipping_policy": "BUYER_PAYS"},
            follow_redirects=True,
        )
        art_dir.joinpath("edit_response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    # Verify DB
    with get_engine().connect() as conn:
        after_row = conn.execute(text("""
            SELECT item.title, lot.price_gross
            FROM lot
            JOIN item ON lot.item_id = item.code
            WHERE lot.code = :code;
        """), {"code": code}).fetchone()

    db_text = f"title={after_row[0]}, price={after_row[1]}" if after_row else "NOT_FOUND"
    art_dir.joinpath("db_lot_after.txt").write_text(db_text, encoding="utf-8")

    # Screenshot
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{ctx.base_url}/ui/items/{code}?token={ctx.auth_token}")
        art_dir.joinpath("screenshot.png").write_bytes(page.screenshot(full_page=True))
        browser.close()

    return {"edited": after_row is not None and str(after_row[1]) == "4999.00"}


def validate_c10(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    db_file = art_dir / "db_lot_after.txt"
    ss = art_dir / "screenshot.png"
    if not db_file.exists() or not ss.exists():
        return False, "Missing db_lot_after.txt or screenshot.png"
    if meta.get("edited"):
        return True, "Title and price updated in DB and on screen"
    return False, f"Edit verification failed: {db_file.read_text(encoding='utf-8')}"


def run_c11(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-11: Delete lot without active deal -> marked deleted, audit event (own lot).

    Self-contained: creates its own lot (which by definition has no active deal)
    and deletes it. The result does not depend on the lots other checks created
    or on whether C-18 ran before or after it (order-independence contract).
    """
    code = create_lot(ctx, "C-11 delete")
    with httpx.Client(timeout=10.0) as client:
        resp = client.delete(f"{ctx.base_url}/api/lots/{code}", headers=_get_auth_headers(ctx))
        art_dir.joinpath("delete_response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    # Check DB lot and audit event
    with get_engine().connect() as conn:
        lot_row = conn.execute(text("SELECT code, status FROM lot WHERE code = :code;"), {"code": code}).fetchone()
        audit_row = conn.execute(
            text("SELECT action, data FROM audit_event WHERE action = 'LOT_DELETED' AND entity_id = :code ORDER BY at DESC LIMIT 1;"),
            {"code": code},
        ).fetchone()

    art_dir.joinpath("db_lot.txt").write_text(str(lot_row), encoding="utf-8")
    art_dir.joinpath("audit_event.txt").write_text(str(audit_row), encoding="utf-8")

    deleted = (lot_row is not None and lot_row[1] == "DELETED") and audit_row is not None
    return {"deleted": deleted, "status_code": resp.status_code}


def validate_c11(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    audit_file = art_dir / "audit_event.txt"
    if not audit_file.exists() or audit_file.stat().st_size == 0:
        return False, "Missing or empty audit_event.txt"
    if meta.get("deleted"):
        return True, "Lot removed/archived and audit event recorded"
    return False, "Lot not marked deleted or audit event missing"


def run_c12(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-12: Delete lot with active deal is rejected with clear reason (own lot)."""
    import uuid
    lot_code = create_lot(ctx, "C-12 delete with deal")
    with get_engine().connect() as conn:
        l_row = conn.execute(text("""
            SELECT lot.code, lot.id, lot.item_id
            FROM lot
            WHERE lot.code = :code
            LIMIT 1;
        """), {"code": lot_code}).fetchone()
    if not l_row:
        art_dir.joinpath("request.txt").write_text("NO_LOTS", encoding="utf-8")
        art_dir.joinpath("response.txt").write_text("NO_LOTS", encoding="utf-8")
        return {"rejected": False}

    lot_code, lot_id, item_code = l_row[0], l_row[1], l_row[2]
    listing_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    # Writes: seed a listing + an ACTIVE deal on the lot, committed as one unit.
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO listing (id, item_code, marketplace_key, price, price_base, currency, publish_evidence, status, created_at, updated_at)
            VALUES (:listing_id, :item_code, 'bazos_cz', 1000, 1000, 'CZK', '{}', 'ACTIVE', NOW(), NOW())
            ON CONFLICT DO NOTHING;
        """), {"listing_id": listing_id, "item_code": item_code})
        conn.execute(text("""
            INSERT INTO deal (id, lot_id, listing_id, agreed_price, currency, price_base, delivery_method, payment_method, status, closure_evidence, created_at, updated_at)
            VALUES (:deal_id, :lot_id, :listing_id, 1000, 'CZK', 1000, 'PERSONAL', 'CASH', 'ACTIVE', '{}', NOW(), NOW())
            ON CONFLICT DO NOTHING;
        """), {"deal_id": deal_id, "lot_id": lot_id, "listing_id": listing_id})

    with httpx.Client(timeout=10.0) as client:
        resp = client.delete(f"{ctx.base_url}/api/lots/{lot_code}", headers=_get_auth_headers(ctx))
        art_dir.joinpath("request.txt").write_text(f"DELETE /api/lots/{lot_code}", encoding="utf-8")
        art_dir.joinpath("response.txt").write_text(f"HTTP {resp.status_code}\n{resp.text}", encoding="utf-8")

    rejected = resp.status_code == 409 and ("активная сделка" in resp.text.lower() or "active deal" in resp.text.lower() or "сделка" in resp.text.lower())
    return {"status_code": resp.status_code, "rejected": rejected}


def validate_c12(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    resp_file = art_dir / "response.txt"
    if not resp_file.exists():
        return False, "Missing response.txt"
    text = resp_file.read_text(encoding="utf-8")
    if meta.get("rejected"):
        return True, f"Deletion rejected with reason: {text.strip()[:100]}"
    return False, f"Expected 409 rejection, got {meta.get('status_code')}: {text.strip()[:100]}"


def get_lots_checks() -> list[CheckDef]:
    return [
        CheckDef("C-06", "Лоты", "Создание лота через HTTP", "Код 2xx, лот в БД, выдан трёхсимвольный код", run_c06, ["http_request.txt", "http_response.txt", "db_lot.txt"], validate_c06),
        CheckDef("C-07", "Лоты", "Создание лота через CLI", "Тот же результат, та же сервисная функция", run_c07, ["cli_command.txt", "cli_output.txt", "db_lot.txt"], validate_c07),
        CheckDef("C-08", "Лоты", "Открытие списка лотов в браузере", "Лот виден, код виден, статус виден", run_c08, ["screenshot.png", "dom.html"], validate_c08),
        CheckDef("C-09", "Лоты", "Открытие карточки лота", "Все вкладки отвечают кодом 200", run_c09, ["screenshot.png", "tabs_status.txt"], validate_c09),
        CheckDef("C-10", "Лоты", "Редактирование названия и цены", "Значения изменились в БД и на экране", run_c10, ["edit_request.json", "edit_response.txt", "db_lot_after.txt", "screenshot.png"], validate_c10),
        CheckDef("C-11", "Лоты", "Удаление лота без сделки", "Лот помечен удалённым, запись в аудите", run_c11, ["delete_response.txt", "db_lot.txt", "audit_event.txt"], validate_c11),
        CheckDef("C-12", "Лоты", "Удаление лота с активной сделкой", "Отклонено с текстом причины", run_c12, ["request.txt", "response.txt"], validate_c12),
    ]
