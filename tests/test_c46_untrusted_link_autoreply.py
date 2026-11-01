"""Regression tests for C-46: Counterparty message with untrusted link suppresses auto-reply (Queue P3).

DoD:
- Any incoming message containing untrusted external URLs must strictly prohibit substantive auto-reply (can_auto_reply = False).
- C-46 selfcheck runner and validator must pass.
- Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nextlife.comms.fraud import detect_fraud
from scripts.selfcheck_audit import run_c46, validate_c46
from scripts.selfcheck_types import CheckContext


def test_untrusted_link_suppresses_auto_reply(tmp_path: Path):
    msg_text = "Please check my payment receipt at http://untrusted-phish.ru/verify"
    res = detect_fraud(msg_text)
    assert res.can_auto_reply is False, "Auto-reply must be strictly suppressed for messages with external links"

    ctx = CheckContext(
        base_url="http://127.0.0.1:8000",
        test_dsn="",
        auth_token="",
        out_dir=tmp_path,
        media_dir=tmp_path / "media",
        temp_dir=tmp_path,
    )
    meta = run_c46(ctx, tmp_path)
    passed, reason = validate_c46(tmp_path, meta)
    assert passed, f"C-46 failed: {reason}"
