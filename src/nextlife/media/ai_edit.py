"""Generative Image AI editing service (TZ §5.4, §11.1).

Provides ai_remove_dust and ai_selling_image via OpenAI-compatible
POST /v1/images/edits endpoint.
Guarantees Tier 1 immutability: ORIGINAL images are never mutated.
Results are saved as derived ItemPhoto versions with ai_generated=True.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from nextlife.core.config import get_data_dir, get_media_dir
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, ItemPhoto

log = logging.getLogger("nextlife.media.ai_edit")

PROMPT_REMOVE_DUST = (
    "Remove dust, lint, specks, scratches, and minor glare from the object surface "
    "while strictly preserving the item's original shape, text, labels, logos, and materials unchanged."
)

PROMPT_SELLING_IMAGE = (
    "Place the subject on a clean studio neutral background with a soft realistic contact shadow underneath, "
    "commercial product photography, strictly keeping the subject itself completely intact."
)


class ImageAINotConfiguredError(ValueError):
    """Raised when Image AI endpoint or key is not configured."""


class ImageAIError(RuntimeError):
    """Raised when Image AI request or parsing fails."""


def _settings_path() -> Path:
    return get_data_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_image_ai_config() -> dict[str, str]:
    """Return Image AI configuration from settings or environment variables."""
    settings = load_settings()
    img_cfg = settings.get("image_ai", {})
    if not isinstance(img_cfg, dict):
        img_cfg = {}

    endpoint = (
        img_cfg.get("endpoint")
        or img_cfg.get("url")
        or os.environ.get("NEXTLIFE_IMAGE_AI_ENDPOINT")
        or ""
    ).strip()

    api_key = (
        img_cfg.get("api_key")
        or img_cfg.get("key")
        or os.environ.get("NEXTLIFE_IMAGE_AI_KEY")
        or ""
    ).strip()

    model = (
        img_cfg.get("model")
        or os.environ.get("NEXTLIFE_IMAGE_AI_MODEL")
        or "dall-e-2"
    ).strip()

    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "model": model,
    }


def is_image_ai_configured() -> bool:
    cfg = get_image_ai_config()
    return bool(cfg.get("endpoint"))


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def call_image_ai_edits(
    image_bytes: bytes,
    prompt: str,
    config: dict[str, str] | None = None,
) -> bytes:
    """Call OpenAI-compatible POST /v1/images/edits endpoint."""
    cfg = config or get_image_ai_config()
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    model = cfg.get("model", "dall-e-2")

    if not endpoint:
        raise ImageAINotConfiguredError("Image AI EndPoint не настроен. Настройте Image AI в Настройках.")

    # Normalize endpoint URL
    url = endpoint.rstrip("/")
    if not url.endswith("/images/edits"):
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        url = f"{url}/images/edits"

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Prepare multipart form
    files = {
        "image": ("image.png", image_bytes, "image/png"),
    }
    data = {
        "prompt": prompt,
        "model": model,
        "response_format": "b64_json",
        "n": 1,
    }

    try:
        response = httpx.post(url, headers=headers, data=data, files=files, timeout=60.0)
    except Exception as exc:
        log.error("Image AI request failed: %s", exc)
        raise ImageAIError(f"Ошибка соединения с Image AI: {exc}") from exc

    if response.status_code != 200:
        err_msg = response.text[:300]
        log.error("Image AI returned status %s: %s", response.status_code, err_msg)
        raise ImageAIError(f"Image AI вернул ошибку {response.status_code}: {err_msg}")

    try:
        res_json = response.json()
        item_data = res_json.get("data", [])[0]
        if "b64_json" in item_data:
            return base64.b64decode(item_data["b64_json"])
        if "url" in item_data:
            img_res = httpx.get(item_data["url"], timeout=30.0)
            img_res.raise_for_status()
            return img_res.content
        raise ValueError("Ни b64_json, ни url не найдены в ответе Image AI")
    except Exception as exc:
        log.error("Failed to parse Image AI response: %s", exc)
        raise ImageAIError(f"Не удалось обработать ответ Image AI: {exc}") from exc


def _save_derived_ai_photo(
    session: Session,
    parent_photo: ItemPhoto,
    generated_png_bytes: bytes,
    step_name: str,
    prompt: str,
) -> ItemPhoto:
    """Save generated image as a new derived ItemPhoto version."""
    dest_dir = get_media_dir() / "items" / parent_photo.item_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    new_filename = f"ai_{uuid.uuid4().hex[:8]}.jpg"
    dest_path = dest_dir / new_filename

    # Convert to high-quality JPEG
    with Image.open(io.BytesIO(generated_png_bytes)) as pil_img:
        rgb_img = pil_img.convert("RGB")
        rgb_img.save(dest_path, format="JPEG", quality=95)
        width, height = rgb_img.size

    sha = _compute_sha256(dest_path)
    new_photo = ItemPhoto(
        id=str(uuid.uuid4()),
        item_code=parent_photo.item_code,
        role=parent_photo.role,
        path=str(dest_path),
        sha256=sha,
        width=width,
        height=height,
        derived_from_id=parent_photo.id,
        ai_generated=True,
        transform_log=[
            {
                "step": step_name,
                "prompt": prompt,
                "parent_id": parent_photo.id,
                "resulting_size": [width, height],
            }
        ],
    )
    session.add(new_photo)

    # Update item photo order: replace parent_photo with new_photo
    item = session.get(Item, parent_photo.item_code)
    if item:
        attrs = dict(item.attrs or {})
        order = list(attrs.get("photo_order") or [])
        if parent_photo.id in order:
            idx = order.index(parent_photo.id)
            order[idx] = new_photo.id
        else:
            order.append(new_photo.id)
        attrs["photo_order"] = order

        if attrs.get("cover_photo_id") == parent_photo.id:
            attrs["cover_photo_id"] = new_photo.id
        item.attrs = attrs

    session.flush()
    return new_photo


def ai_remove_dust(
    photo_id: str,
    session: Session | None = None,
) -> ItemPhoto:
    """Apply generative AI dust removal to photo, saving as new version."""
    def _run(s: Session) -> ItemPhoto:
        photo = s.get(ItemPhoto, photo_id)
        if not photo:
            raise ValueError(f"Фото {photo_id} не найдено")

        src_path = Path(photo.path)
        if not src_path.exists():
            raise FileNotFoundError(f"Файл фото {photo.path} не существует")

        # Prepare PNG bytes for OpenAI API
        with Image.open(src_path) as img:
            buf = io.BytesIO()
            img.convert("RGBA").save(buf, format="PNG")
            png_bytes = buf.getvalue()

        edited_bytes = call_image_ai_edits(
            image_bytes=png_bytes,
            prompt=PROMPT_REMOVE_DUST,
        )

        return _save_derived_ai_photo(
            session=s,
            parent_photo=photo,
            generated_png_bytes=edited_bytes,
            step_name="ai_remove_dust",
            prompt=PROMPT_REMOVE_DUST,
        )

    if session is not None:
        return _run(session)
    with get_db_session() as s:
        res = _run(s)
        s.commit()
        s.refresh(res)
        return res


def ai_selling_image(
    photo_id: str,
    session: Session | None = None,
) -> ItemPhoto:
    """Apply generative AI clean background with soft shadow, saving as new version."""
    def _run(s: Session) -> ItemPhoto:
        photo = s.get(ItemPhoto, photo_id)
        if not photo:
            raise ValueError(f"Фото {photo_id} не найдено")

        src_path = Path(photo.path)
        if not src_path.exists():
            raise FileNotFoundError(f"Файл фото {photo.path} не существует")

        with Image.open(src_path) as img:
            buf = io.BytesIO()
            img.convert("RGBA").save(buf, format="PNG")
            png_bytes = buf.getvalue()

        edited_bytes = call_image_ai_edits(
            image_bytes=png_bytes,
            prompt=PROMPT_SELLING_IMAGE,
        )

        return _save_derived_ai_photo(
            session=s,
            parent_photo=photo,
            generated_png_bytes=edited_bytes,
            step_name="ai_selling_image",
            prompt=PROMPT_SELLING_IMAGE,
        )

    if session is not None:
        return _run(session)
    with get_db_session() as s:
        res = _run(s)
        s.commit()
        s.refresh(res)
        return res


def undo_photo(session: Session, photo_id: str) -> ItemPhoto | None:
    """Revert photo to previous version in derived_from_id chain (§5.4)."""
    photo = session.get(ItemPhoto, photo_id)
    if not photo or not photo.derived_from_id:
        return None

    parent = session.get(ItemPhoto, photo.derived_from_id)
    if not parent:
        return None

    # Update item references back to parent
    item = session.get(Item, photo.item_code)
    if item:
        attrs = dict(item.attrs or {})
        order = list(attrs.get("photo_order") or [])
        if photo.id in order:
            idx = order.index(photo.id)
            order[idx] = parent.id
            attrs["photo_order"] = order
        if attrs.get("cover_photo_id") == photo.id:
            attrs["cover_photo_id"] = parent.id
        item.attrs = attrs

    # Delete the derived photo file and DB row
    p_path = Path(photo.path)
    session.delete(photo)
    session.flush()

    if p_path.exists():
        try:
            p_path.unlink()
        except OSError:
            pass

    return parent
