"""Shared helpers for selfcheck checks that need a lot and/or a photo.

DESIGN (C-18 redesign, segment 0.5 — the "who is wrong" rule applied to the
instrument, not the product):

Every selfcheck check is SELF-CONTAINED. A check that needs a lot CREATES ITS
OWN lot via HTTP and operates strictly by the 3-char code returned at creation.
A check that needs media UPLOADS ITS OWN photo to that lot.

There is NO "pick the newest item" selector anywhere in selfcheck. That pattern
(ORDER BY created_at DESC LIMIT 1, with or without a status filter) coupled a
check's result to the execution order of the checks that ran before it: C-11
soft-deletes the newest lot, so a later "newest" check silently retargeted a
different item (or a just-deleted one) and false-FAILED. Attempt #2 fixed 2 of
the 4 copies of the selector and produced 7 new failures for exactly this
reason. Fixing the instrument by editing product code is forbidden (see the
false-FAIL rule); the correct fix is to make the instrument independent of run
order, which is what this module provides.

Order-independence contract: reversing the order of any two checks (e.g. C-11
vs C-18) must not change either's result, because no check reads or mutates
another check's lots.
"""

import io
from typing import Any

import httpx
from PIL import Image
from scripts.selfcheck_types import CheckContext


def make_jpeg(color: str = "red", size: tuple[int, int] = (640, 480)) -> bytes:
    """A deterministic in-memory JPEG (no EXIF/GPS) for upload checks."""
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _auth_headers(ctx: CheckContext) -> dict[str, str]:
    return {"Authorization": f"Bearer {ctx.auth_token}"}


def create_lot(ctx: CheckContext, title: str) -> str:
    """Create a lot via HTTP and return the 3-char item code obtained at creation.

    An explicit currency keeps the check independent of the marketplace seed
    state (create_item_and_lot only derives currency from enabled marketplaces
    when none is supplied).
    """
    payload = {
        "title": title,
        "category": "electronics",
        "price": 1000.0,
        "currency": "CZK",
        "condition": "used",
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(f"{ctx.base_url}/api/lots", json=payload, headers=_auth_headers(ctx))
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"create_lot failed: HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    code = data.get("item_code") or data.get("lot_code") or ""
    if len(code) != 3:
        raise RuntimeError(f"create_lot: no 3-char code in response: {data}")
    return code


def _post_photos(ctx: CheckContext, code: str, files: list[tuple[str, bytes]], role: str = "ORIGINAL") -> tuple[int, list[dict[str, Any]]]:
    # The API's list parameter is the multipart field "files" (plural); the
    # singular "file" field only ever carries ONE upload, which is why the
    # multi-photo lightbox checks must send under "files".
    multipart = [("files", (name, data, "image/jpeg")) for name, data in files]
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            f"{ctx.base_url}/api/items/{code}/photos",
            files=multipart,
            data={"role": role},
            headers=_auth_headers(ctx),
        )
    photos = resp.json().get("photos", []) if resp.status_code in (200, 201) else []
    return resp.status_code, photos


def upload_photo_api(
    ctx: CheckContext,
    code: str,
    filename: str = "media.jpg",
    data: bytes | None = None,
    color: str = "red",
) -> tuple[int, list[dict[str, Any]]]:
    """Upload one photo to the lot via the JSON API. Returns (status_code, photos)."""
    if data is None:
        data = make_jpeg(color)
    return _post_photos(ctx, code, [(filename, data)])


def upload_photos_api(ctx: CheckContext, code: str, colors: list[str]) -> tuple[int, list[dict[str, Any]]]:
    """Upload several photos (separate chains) to the lot. Returns (status_code, photos).

    Used by the lightbox checks, which need >=2 photos so that next/prev/arrow
    navigation actually changes the displayed image.
    """
    files = [(f"p{i}.jpg", make_jpeg(c)) for i, c in enumerate(colors)]
    return _post_photos(ctx, code, files)


def photo_id_from_response(photos: list[dict[str, Any]]) -> str:
    """Return the first photo id of a lot from an upload response ('' if none)."""
    for p in photos:
        pid = p.get("id")
        if pid:
            return str(pid)
    return ""
