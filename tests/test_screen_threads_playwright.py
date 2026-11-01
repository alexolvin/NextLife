"""Playwright mobile test for Messaging Threads Screen (T-056, Section V.2 item 8).

DoD:
- Screen rendered on 360 px mobile viewport.
- Fraud suspicion badge (fraud_suspected) is prominently displayed and visible WITHOUT scrolling (above the fold).
- Original message and Russian translation displayed side-by-side / paired.
- Three action buttons present: SEND, EDIT, DECLINE.
- Interaction test with owner command execution.
- Module length <= 400 lines.
"""

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy import select

from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Listing, Message, Outbox, Thread

BASE_URL = os.environ.get("NEXTLIFE_BASE_URL", "http://127.0.0.1:8081")
TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")
SCREENSHOT_DIR = Path(".a/tmp/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _seed_fraud_and_escalated_thread():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Playwright Chat Test Item",
            category_key="electronics",
            price=Decimal("8900.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        listing = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key="bazos_cz",
            external_id=f"bazos-test-{lot_code}",
            external_url="https://bazos.cz/item/999",
            price=Decimal("8900.00"),
            currency="CZK",
            price_base=Decimal("8900.00"),
            status="PUBLISHED",
            published_at=datetime.now(UTC),
        )
        session.add(listing)
        session.flush()

        thread = Thread(
            id=str(uuid.uuid4()),
            listing_id=listing.id,
            channel="bazos_cz",
            counterparty="podvodnik@fake-bank.cz",
            lang="cs",
            status="ACTIVE",
        )
        session.add(thread)
        session.flush()

        msg = Message(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            direction="IN",
            transport="BAZOS_CZ",
            text_original="Dobrý den, zaplatil jsem přes kurýra DPD na odkazu: http://dpd-cz-platba.fake/pay",
            lang_original="cs",
            text_ru="[RU] Здравствуйте, я оплатил через курьера DPD по ссылке: http://dpd-cz-platba.fake/pay",
            sent_at=datetime.now(UTC),
            escalated=True,
            answered_by=None,
            source_evidence={
                "fraud_suspected": True,
                "fraud_reasons": ["Фейковый платёжный шлюз доставки (DPD phishing)"],
                "escalation_status": "AWAITING_OWNER_COMMAND",
                "prepared_response": "Dobrý den, na neznámé odkazy neklikám a platby přijímám pouze na svůj IBAN.",
                "lot_code": lot_code,
            },
        )
        session.add(msg)
        session.commit()
        return lot_code, thread.id, msg.id


def test_threads_screen_mobile_playwright(live_server_url):
    lot_code, thread_id, msg_id = _seed_fraud_and_escalated_thread()

    lib_path = str(Path(__file__).resolve().parent.parent / "lib" / "playwright_libs")
    custom_env = dict(os.environ)
    custom_env["LD_LIBRARY_PATH"] = f"{lib_path}:{custom_env.get('LD_LIBRARY_PATH', '')}".strip(":")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, env=custom_env)
        # Emulate compact mobile viewport (360x740 px)
        context = browser.new_context(
            viewport={"width": 360, "height": 740},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()

        # Step 1: Open Threads screen with auth token
        base_url = live_server_url or BASE_URL
        url = f"{base_url}/ui/threads?thread_id={thread_id}&token={TOKEN}"
        resp = page.goto(url, wait_until="networkidle")
        assert resp and resp.status == 200, f"Page failed to load: {resp.status if resp else None}"

        # Step 2: Verify Red Fraud Banner is visible without scrolling (above the fold)
        fraud_badge = page.locator('[data-testid="fraud-suspected-badge"]')
        assert fraud_badge.is_visible(), "Fraud suspected banner is not visible!"

        box = fraud_badge.bounding_box()
        assert box is not None, "Failed to get bounding box for fraud badge"
        assert box["y"] >= 0, "Fraud badge is positioned above the visible viewport"
        assert (box["y"] + box["height"]) <= 500, (
            f"DoD violation: fraud badge (y={box['y']}, h={box['height']}) is not visible without scrolling on 360px viewport!"
        )

        # Step 3: Verify Original text and Russian translation are both visible
        orig_text = page.locator('[data-testid="msg-original-text"]')
        trans_text = page.locator('[data-testid="msg-translated-text"]')
        assert orig_text.is_visible()
        assert trans_text.is_visible()
        assert "zaplatil jsem" in orig_text.inner_text()
        assert "Здравствуйте" in trans_text.inner_text()

        # Step 4: Verify Three action buttons (SEND, EDIT, DECLINE)
        btn_send = page.locator('[data-testid="btn-send"]')
        btn_edit = page.locator('[data-testid="btn-edit"]')
        btn_decline = page.locator('[data-testid="btn-decline"]')

        assert btn_send.is_visible()
        assert btn_edit.is_visible()
        assert btn_decline.is_visible()

        # Step 5: Test EDIT workflow
        btn_edit.click()
        custom_textarea = page.locator('[data-testid="edit-custom-textarea"]')
        assert custom_textarea.is_visible()

        # Save screenshot of mobile view before submission
        screenshot_path = SCREENSHOT_DIR / "t056_threads_360px.png"
        page.screenshot(path=str(screenshot_path), full_page=False)
        assert screenshot_path.exists()

        # Submit custom edited text
        custom_textarea.fill("Dobrý den, podvodné odkazy neotvírám. Inzerát platí.")
        btn_submit_edit = page.locator('[data-testid="btn-submit-edit"]')
        with page.expect_navigation():
            btn_submit_edit.click()

        # Verify execution in DB
        with get_db_session() as session:
            updated_msg = session.get(Message, msg_id)
            assert updated_msg.answered_by == "OWNER"
            assert updated_msg.source_evidence["escalation_status"] == "EDITED_AND_SENT"

            # Check outbound message created
            out_msg = session.scalar(
                select(Message).where(Message.thread_id == thread_id, Message.direction == "OUT")
            )
            assert out_msg is not None
            assert "podvodné odkazy" in out_msg.text_original

            # Check outbox item created
            outbox_entry = session.scalar(
                select(Outbox).where(Outbox.recipient_hint == "podvodnik@fake-bank.cz")
            )
            assert outbox_entry is not None
            assert "podvodné odkazy" in outbox_entry.body

        browser.close()
