"""Regression tests for C-38: Adding delivery option and buyer total recalculation (Queue P4).

DoD:
- POST /api/lots/{code}/shipping-options supports adding delivery options (including single option dict).
- GET /api/lots/{code}/buyer-total calculates buyer total with delivery cost.
- C-38 selfcheck runner and validator must pass.
- Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from nextlife.core.secrets import resolve_secret
from scripts.selfcheck_desc import run_c38, validate_c38
from scripts.selfcheck_types import CheckContext


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    port = find_free_port()
    # NEXTLIFE_DSN is already in os.environ (set by the session fixture).
    env = dict(os.environ)
    env["NEXTLIFE_PORT"] = str(port)
    env["NEXTLIFE_WEB_TOKEN"] = os.environ.get("NEXTLIFE_WEB_TOKEN", "test_ephemeral_token_secret")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "nextlife.api.main:create_app",
            "--factory",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            with httpx.Client(timeout=1.0) as client:
                if client.get(f"{url}/health").status_code == 200:
                    break
        except Exception:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait()


def test_add_delivery_option_and_buyer_total(live_server: str, tmp_path: Path):
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    dsn = os.environ.get("NEXTLIFE_DSN", "")

    # Create test lot. An explicit currency keeps this test independent of the
    # marketplace seed state (create_item_and_lot only derives currency from
    # active marketplaces when none is supplied). We operate on the lot this
    # test created, by the code the API returned — not on "newest item", which
    # is cross-run-DB-state dependent.
    with httpx.Client(timeout=5.0) as client:
        r = client.post(
            f"{live_server}/api/lots",
            json={
                "title": "Shipping Option Test Lot",
                "category": "books",
                "price": 500,
                "currency": "CZK",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (200, 201)
        item_code = r.json()["item_code"]

    ctx = CheckContext(
        base_url=live_server,
        test_dsn=dsn,
        auth_token=token,
        out_dir=tmp_path,
        media_dir=tmp_path / "media",
        temp_dir=tmp_path,
    )
    meta = run_c38(ctx, tmp_path, code=item_code)
    passed, reason = validate_c38(tmp_path, meta)
    assert passed, f"C-38 failed: {reason}"
