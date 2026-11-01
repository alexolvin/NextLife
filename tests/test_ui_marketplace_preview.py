"""Regression tests for C-33: Preview 'as on marketplace' (Phase P1: Media).

Verifies that /ui/items/{code}/preview/{marketplace_key} route returns 200 and renders marketplace preview with variants.
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
from scripts.selfcheck_media_advanced import run_c33, validate_c33
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


def test_marketplace_preview(live_server: str, tmp_path: Path):
    token = resolve_secret("NEXTLIFE_WEB_TOKEN")
    dsn = os.environ.get("NEXTLIFE_DSN", "")

    # Create lot with a photo
    with httpx.Client(timeout=5.0) as client:
        r = client.post(
            f"{live_server}/api/lots",
            json={"title": "Marketplace Preview Test", "category": "electronics", "price": 1000},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (200, 201)
        code = r.json().get("code") or r.json().get("lot_code")

        buf = io.BytesIO()
        Image.new("RGB", (640, 480), "green").save(buf, "JPEG")

        r_upload = client.post(
            f"{live_server}/api/items/{code}/photos",
            files={"files": ("test_preview.jpg", buf.getvalue(), "image/jpeg")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r_upload.status_code in (200, 201)

    ctx = CheckContext(
        base_url=live_server,
        test_dsn=dsn,
        auth_token=token,
        out_dir=tmp_path,
        media_dir=Path("data/media"),
        temp_dir=tmp_path,
    )
    meta = run_c33(ctx, tmp_path)
    passed, reason = validate_c33(tmp_path, meta)
    assert passed, f"C-33 failed: {reason}"
