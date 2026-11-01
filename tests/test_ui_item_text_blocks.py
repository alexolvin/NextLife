"""Regression tests for C-35: Item card shows notes, generated text, and final text (Queue P2).

Verifies that /ui/items/{code} displays operator notes, generated draft description, and editable final text.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
lib_path = str(ROOT_DIR / "lib" / "playwright_libs")
if Path(lib_path).exists():
    existing_ld = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{lib_path}:{existing_ld}".strip(":")

import httpx
import pytest

from nextlife.core.secrets import resolve_secret
from scripts.selfcheck_desc import run_c35, validate_c35
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


def test_item_detail_text_blocks(live_server: str, tmp_path: Path):
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    dsn = os.environ.get("NEXTLIFE_DSN", "")

    # Create lot with notes
    with httpx.Client(timeout=5.0) as client:
        r = client.post(
            f"{live_server}/api/lots",
            json={
                "title": "Text Blocks Test Lot",
                "category": "electronics",
                "price": 1200,
                "notes": "Операторские заметки по состоянию устройства",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (200, 201)
        code = r.json().get("code") or r.json().get("lot_code")

    ctx = CheckContext(
        base_url=live_server,
        test_dsn=dsn,
        auth_token=token,
        out_dir=tmp_path,
        media_dir=Path("data/media"),
        temp_dir=tmp_path,
    )
    meta = run_c35(ctx, tmp_path)
    passed, reason = validate_c35(tmp_path, meta)
    assert passed, f"C-35 failed: {reason}"


def test_item_detail_text_blocks_without_notes(live_server: str, tmp_path: Path):
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    dsn = os.environ.get("NEXTLIFE_DSN", "")

    # Create lot without notes
    with httpx.Client(timeout=5.0) as client:
        r = client.post(
            f"{live_server}/api/lots",
            json={
                "title": "No Notes Test Lot",
                "category": "electronics",
                "price": 800,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (200, 201)

    ctx = CheckContext(
        base_url=live_server,
        test_dsn=dsn,
        auth_token=token,
        out_dir=tmp_path,
        media_dir=Path("data/media"),
        temp_dir=tmp_path,
    )
    meta = run_c35(ctx, tmp_path)
    passed, reason = validate_c35(tmp_path, meta)
    assert passed, f"C-35 failed: {reason}"
