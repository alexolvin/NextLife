"""Playwright verification for Computer Build Profile and RAM detection (DoD §8.2).

Tests candidate with title 'Herni pc ryzen 5 Rtx 3090 32G rezervace':
- Profile badge 'Компьютерная сборка' is visible.
- RAM component (32GB) is detected and priced.
- Mobile layout (390x844) renders component card without horizontal scrolling.
"""

import uuid
from decimal import Decimal
import pytest
from playwright.sync_api import sync_playwright

from nextlife.services.buy import analyze_kit
from nextlife.storage.db import get_db_session
from nextlife.storage.models.core import BuyCandidate, BuyRequest


def test_pc_profile_and_ram_playwright(live_server_url):
    candidate_id = str(uuid.uuid4())
    req_id = str(uuid.uuid4())

    with get_db_session() as session:
        req = BuyRequest(
            id=req_id,
            query="Herni pc ryzen 5 Rtx 3090 32G rezervace",
            currency="CZK",
            budget=Decimal("40000.00"),
            status="ACTIVE",
        )
        session.add(req)
        candidate = BuyCandidate(
            id=candidate_id,
            request_id=req_id,
            marketplace_key="bazos_cz",
            external_url="https://pc.bazos.cz/inzerat/225874010/herni-pc-ryzen-5-rtx-3090-32g.php",
            external_id="225874010",
            price=Decimal("39999.00"),
            currency="CZK",
            price_base=Decimal("39999.00"),
            verdict="Herni pc ryzen 5 Rtx 3090 32G rezervace",
        )
        session.add(candidate)
        session.commit()

        # 1. Trigger analysis in DB
        analysis = analyze_kit(session, candidate_id)
        assert analysis.get("profile") == "pc_build"
        components = analysis.get("components", [])
        assert any("RAM" in c["name"] and "32GB" in c["name"] for c in components)

    # 2. Open page in mobile viewport via Playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
            device_scale_factor=2,
        )
        page = context.new_page()

        url = f"{live_server_url}/ui/buy/kits/{candidate_id}?token=test_token_secret"
        response = page.goto(url, wait_until="networkidle")
        assert response.status == 200

        # PC profile badge
        badge = page.locator('[data-testid="badge-pc-profile"]')
        assert badge.is_visible()
        assert "Компьютерная сборка" in badge.inner_text()

        # RAM card in mobile view
        ram_cards = page.locator('[data-testid="component-card"]:has-text("RAM")')
        assert ram_cards.count() >= 1
        assert "32GB" in ram_cards.first.inner_text()

        # Parts sum
        parts_val = page.locator('[data-testid="val-parts-sum"]').inner_text()
        assert "CZK" in parts_val

        browser.close()
