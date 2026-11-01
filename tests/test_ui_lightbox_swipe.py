"""Regression tests for C-30: Lightbox mobile swipe navigation (Phase P1: Media).

Verifies that swiping on 360px width in open lightbox changes the displayed image.
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
from PIL import Image
import pytest

from nextlife.core.secrets import resolve_secret
from scripts.selfcheck_media_advanced import run_c30, validate_c30
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


def test_lightbox_swipe(live_server: str, tmp_path: Path):
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    dsn = os.environ.get("NEXTLIFE_DSN", "")

    # Create lot with 2 distinct photos
    with httpx.Client(timeout=5.0) as client:
        r = client.post(
            f"{live_server}/api/lots",
            json={"title": "Lightbox Swipe Test", "category": "electronics", "price": 1000},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (200, 201)
        code = r.json().get("code") or r.json().get("lot_code")

        buf1 = io.BytesIO()
        Image.new("RGB", (100, 100), "red").save(buf1, "JPEG")
        buf2 = io.BytesIO()
        Image.new("RGB", (100, 100), "blue").save(buf2, "JPEG")

        r1 = client.post(
            f"{live_server}/api/items/{code}/photos",
            files={"files": ("p1.jpg", buf1.getvalue(), "image/jpeg")},
            headers={"Authorization": f"Bearer {token}"},
        )
        r2 = client.post(
            f"{live_server}/api/items/{code}/photos",
            files={"files": ("p2.jpg", buf2.getvalue(), "image/jpeg")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code in (200, 201)
        assert r2.status_code in (200, 201)

    ctx = CheckContext(
        base_url=live_server,
        test_dsn=dsn,
        auth_token=token,
        out_dir=tmp_path,
        media_dir=Path("data/media"),
        temp_dir=tmp_path,
    )
    meta = run_c30(ctx, tmp_path)
    passed, reason = validate_c30(tmp_path, meta)
    assert passed, f"C-30 failed: {reason}"
