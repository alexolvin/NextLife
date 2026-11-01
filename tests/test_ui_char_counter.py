"""Regression tests for C-36: Character counter and marketplace limit in draft editor (Queue P2).

Verifies that /ui/draft/{code} displays a character counter with limits from marketplace config.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

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
from scripts.selfcheck_desc import run_c36, validate_c36
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


def test_char_counter_and_limit_present_in_draft_editor(live_server: str, tmp_path: Path):
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    dsn = os.environ.get("NEXTLIFE_DSN", "")

    # Create test lot
    with httpx.Client(timeout=5.0) as client:
        r = client.post(
            f"{live_server}/api/lots",
            json={
                "title": "Char Counter Test Lot",
                "category": "electronics",
                "price": 950,
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
    meta = run_c36(ctx, tmp_path)
    passed, reason = validate_c36(tmp_path, meta)
    assert passed, f"C-36 failed: {reason}"
