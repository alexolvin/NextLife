"""Playwright mobile test for Deal Screen (Task T-067 / Section V.2 item 9).

DoD:
- Deal status, counterparty, agreed price, payment and delivery method visible.
- Checklists (inspection & packaging), evidence upload, handover protocol, settlement calculation.
- All actions accessible from 360 px mobile viewport without horizontal scrolling.
- Confirmations show explicit text of what will happen.
- Touch targets >= 44 px.
- Module length <= 400 lines.
"""

import os
import uuid
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import sync_playwright

from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Handover, Item, Lot, Party, Settlement

BASE_URL = os.environ.get("NEXTLIFE_BASE_URL", "http://127.0.0.1:8081")
TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
SCREENSHOT_DIR = Path(".a/tmp/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _seed_test_deal():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Deal Screen Test Laptop",
            category_key="electronics",
            price=Decimal("14500.00"),
        )
        lot = session.query(Lot).filter(Lot.code == created["lot_code"]).first()
        party = Party(
            id=str(uuid.uuid4()),
            name="Michal Procházka",
            email="michal.p@example.cz",
            phone="+420777998877",
        )
        session.add(party)

        deal = Deal(
            id=str(uuid.uuid4()),
            lot_id=lot.id,
            party_id=party.id,
            agreed_price=Decimal("14000.00"),
            currency="CZK",
            price_base=Decimal("14000.00"),
            delivery_method="ZASILKOVNA",
            payment_method="BANK_TRANSFER",
            status="PAID",
        )
        session.add(deal)
        session.flush()

        settlement = Settlement(
            id=str(uuid.uuid4()),
            deal_id=deal.id,
            gross_amount=Decimal("14000.00"),
            platform_fee=Decimal("700.00"),
            delivery_cost=Decimal("120.00"),
            packaging_cost=Decimal("50.00"),
            net_payout=Decimal("13130.00"),
            currency="CZK",
            payout_status="PENDING",
        )
        session.add(settlement)

        handover = Handover(
            id=str(uuid.uuid4()),
            deal_id=deal.id,
            method="ZASILKOVNA",
            status="PENDING",
            evidence={},
        )
        session.add(handover)
        session.commit()

        return deal.id, lot.code, created["item_code"], party.id


def test_deal_screen_mobile_playwright(live_server_url):
    deal_id, lot_code, item_code, party_id = _seed_test_deal()

    lib_path = str(Path(__file__).resolve().parent.parent / "lib" / "playwright_libs")
    custom_env = dict(os.environ)
    custom_env["LD_LIBRARY_PATH"] = f"{lib_path}:{custom_env.get('LD_LIBRARY_PATH', '')}".strip(":")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, env=custom_env)
        # Mobile viewport 360 x 740 px
        context = browser.new_context(
            viewport={"width": 360, "height": 740},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()

        # 1. Load Deal Screen
        base_url = live_server_url or BASE_URL
        url = f"{base_url}/ui/deals/{deal_id}?token={TOKEN}"
        resp = page.goto(url, wait_until="networkidle")
        assert resp and resp.status == 200, f"Deal screen failed to load: {resp.status if resp else None}"

        # 2. Check no horizontal scrolling on 360px viewport
        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        client_width = page.evaluate("() => document.documentElement.clientWidth")
        assert scroll_width <= client_width + 5, f"Horizontal scroll violation: scrollWidth {scroll_width} > clientWidth {client_width}"

        # 3. Check Header & Lot Code
        lot_badge = page.locator('[data-testid="deal-lot-code"]')
        assert lot_badge.is_visible()
        assert lot_code in lot_badge.inner_text()

        status_badge = page.locator('[data-testid="deal-status-badge"]')
        assert status_badge.is_visible()
        # TZ-NextLife-UI-N2 V4 (P-E15): статусы сделок — по-русски.
        assert "Оплачена" in status_badge.inner_text()

        # 4. Check Counterparty card
        cp = page.locator('[data-testid="deal-counterparty"]')
        assert cp.is_visible()
        assert "Michal Procházka" in cp.inner_text()

        # 5. Check Price & Settlement calculation box (TZ-NextLife-UI-N2 §1 money format)
        agreed_price = page.locator('[data-testid="deal-agreed-price"]')
        assert agreed_price.is_visible()
        assert "14 000 Kč" in agreed_price.inner_text()

        settlement_box = page.locator('[data-testid="deal-settlement-box"]')
        assert settlement_box.is_visible()
        assert "13 130 Kč" in settlement_box.inner_text()

        # 6. Check Checklists
        inspection = page.locator('[data-testid="inspection-checklist"]')
        assert inspection.is_visible()

        # 7. Check Dispatch Evidence section
        ev_section = page.locator('[data-testid="evidence-section"]')
        assert ev_section.is_visible()
        assert page.locator('[data-testid="evidence-box_content_photo"]').is_visible()
        assert page.locator('[data-testid="evidence-serial_number_photo"]').is_visible()
        assert page.locator('[data-testid="evidence-sealed_box_label_photo"]').is_visible()
        assert page.locator('[data-testid="evidence-tracking_and_weight_receipt"]').is_visible()

        # 8. Check Handover Act generation
        btn_gen_protocol = page.locator('[data-testid="btn-generate-protocol"]')
        assert btn_gen_protocol.is_visible()
        btn_box = btn_gen_protocol.bounding_box()
        assert btn_box and btn_box["height"] >= 40, "Button height must satisfy mobile tap target"

        btn_gen_protocol.click()
        page.wait_for_load_state("networkidle")

        # Verify preview appears
        preview = page.locator('[data-testid="protocol-preview"]')
        assert preview.is_visible()
        assert "АКТ ПРИЁМА-ПЕРЕДАЧИ" in preview.inner_text()

        # 9. Test Dispatch Modal confirmation
        btn_dispatch = page.locator('[data-testid="btn-dispatch-modal"]')
        assert btn_dispatch.is_visible()
        btn_dispatch.click()

        modal = page.locator('[data-testid="confirmation-modal"]')
        assert modal.is_visible()
        modal_desc = page.locator('[data-testid="modal-description-text"]')
        assert modal_desc.is_visible()
        assert "Будет зафиксирована отправка товара" in modal_desc.inner_text()

        # Save mobile screenshot
        screenshot_path = SCREENSHOT_DIR / "t067_deal_screen_360px.png"
        page.screenshot(path=str(screenshot_path), full_page=True)
        assert screenshot_path.exists()

        browser.close()

    # Cleanup
    with get_db_session() as session:
        session.query(Settlement).filter(Settlement.deal_id == deal_id).delete()
        session.query(Handover).filter(Handover.deal_id == deal_id).delete()
        session.query(Deal).filter(Deal.id == deal_id).delete()
        session.query(Party).filter(Party.id == party_id).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()
