"""Regression tests for C-44: Payment screenshot escalates and does not mark PAID (Queue P3).

DoD:
- Counterparty screenshot is strictly not proof of payment.
- Deal does not transition to PAID.
- Escalation to owner is created.
- Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nextlife.core.secrets import resolve_secret
from scripts.selfcheck_audit import run_c44, validate_c44
from scripts.selfcheck_types import CheckContext


def test_payment_screenshot_escalates_and_keeps_deal_unpaid(tmp_path: Path):
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    dsn = os.environ.get("NEXTLIFE_DSN", "")

    ctx = CheckContext(
        base_url="http://127.0.0.1:8000",
        test_dsn=dsn,
        auth_token=token,
        out_dir=tmp_path,
        media_dir=tmp_path / "media",
        temp_dir=tmp_path,
    )
    meta = run_c44(ctx, tmp_path)
    passed, reason = validate_c44(tmp_path, meta)
    assert passed, f"C-44 failed: {reason}"
