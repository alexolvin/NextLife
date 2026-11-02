"""Generative Image AI editing service (TZ §5.4, §11.1).

Provides ai_remove_dust, ai_selling_image, and ai_replace_bg supporting
OpenAI, Google Gemini, and Cloudflare Workers AI.
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

from PIL import Image
from sqlalchemy.orm import Session

from nextlife.core.config import get_data_dir, get_media_dir
from nextlife.media.ai_providers import (
    ImageAIError,
    call_cloudflare_image,
    call_gemini_content,
    call_gemini_predict,
    call_openai_image,
)
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

PROMPT_REPLACE_BG = (
    "Isolate the main subject and replace the background with a clean, modern, professional neutral studio background, "
    "keeping the subject completely intact with natural realistic shadows."
)


class ImageAINotConfiguredError(ValueError):
    """Raised when Image AI endpoint or key is not configured."""


def load_settings() -> dict[str, Any]:
    path = get_data_dir() / "settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_image_ai_config() -> dict[str, str]:
    """Return Image AI configuration from settings or environment variables."""
    cfg = load_settings().get("image_ai", {})
    if not isinstance(cfg, dict):
        cfg = {}
    provider = (cfg.get("provider") or os.environ.get("NEXTLIFE_IMAGE_AI_PROVIDER") or "openai").strip().lower()
    endpoint = (cfg.get("endpoint") or cfg.get("url") or os.environ.get("NEXTLIFE_IMAGE_AI_ENDPOINT") or "").strip()
    api_key = (cfg.get("api_key") or cfg.get("key") or os.environ.get("NEXTLIFE_IMAGE_AI_KEY") or "").strip()
    default_model = "gemini-2.5-flash-image" if provider == "gemini" else ("@cf/runwayml/stable-diffusion-v1-5-inpainting" if provider == "cloudflare" else "dall-e-2")
    base_model = (cfg.get("model") or default_model).strip()
    default_selling = "@cf/bria/remove-background" if provider == "cloudflare" else base_model
    default_bg = "@cf/bria/remove-background" if provider == "cloudflare" else base_model
    return {
        "provider": provider,
        "endpoint": endpoint,
        "api_key": api_key,
        "model": base_model,
        "model_dust": (cfg.get("model_dust") or base_model).strip(),
        "model_selling": (cfg.get("model_selling") or default_selling).strip(),
        "model_bg": (cfg.get("model_bg") or default_bg).strip(),
    }


def is_image_ai_configured() -> bool:
    cfg = get_image_ai_config()
    provider = cfg.get("provider", "openai")
    if provider == "gemini":
        return bool(cfg.get("endpoint") or cfg.get("api_key"))
    return bool(cfg.get("endpoint"))


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


# Backward-compatible aliases for tests
_call_gemini_predict = call_gemini_predict
_call_gemini_content = call_gemini_content


def _call_gemini_image(prompt: str, image_bytes: bytes, cfg: dict[str, str], model_override: str | None = None) -> bytes:
    endpoint = (cfg.get("endpoint") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    target_model = (model_override or cfg.get("model") or "gemini-2.5-flash-image").strip()

    if not endpoint and not api_key:
        raise ImageAINotConfiguredError("Image AI EndPoint или API Key не настроен. Настройте Image AI в Настройках.")

    base_url = endpoint.rstrip("/") if endpoint else "https://generativelanguage.googleapis.com"
    if "/v1beta/models" in base_url:
        base_url = base_url.split("/v1beta/models")[0]

    if ":predict" in endpoint or target_model.startswith("imagen-"):
        try:
            return call_gemini_predict(target_model, prompt, base_url, api_key, endpoint)
        except Exception as exc:
            err_msg = str(exc)
            if "404" in err_msg or "not supported" in err_msg:
                log.warning("Imagen predict 404 for %s, falling back to Gemini generateContent", target_model)
                try:
                    return call_gemini_content("gemini-2.5-flash-image", prompt, image_bytes, base_url, api_key, "")
                except Exception:
                    pass
                raise ImageAIError(
                    f"Image AI (Gemini) вернул ошибку 404: модель '{target_model}' не найдена или не поддерживает метод predict. "
                    "В Google AI Studio для генерации и редактирования изображений укажите 'gemini-2.5-flash-image' "
                    "в Настройках Image AI (или подключите платёжный аккаунт GCP для Imagen 3)."
                ) from exc
            raise

    return call_gemini_content(target_model, prompt, image_bytes, base_url, api_key, endpoint)


def _call_gemini_imagen(prompt: str, cfg: dict[str, str], image_bytes: bytes = b"") -> bytes:
    return _call_gemini_image(prompt=prompt, image_bytes=image_bytes, cfg=cfg)


def call_image_ai_edits(
    image_bytes: bytes,
    prompt: str,
    config: dict[str, str] | None = None,
    model_override: str | None = None,
) -> bytes:
    """Call generative Image AI endpoint (OpenAI, Google Gemini, or Cloudflare Workers AI)."""
    cfg = config or get_image_ai_config()
    provider = (cfg.get("provider") or "openai").strip().lower()

    if provider == "gemini":
        return _call_gemini_image(prompt=prompt, image_bytes=image_bytes, cfg=cfg, model_override=model_override)
    if provider == "cloudflare":
        return call_cloudflare_image(prompt=prompt, image_bytes=image_bytes, cfg=cfg, model=model_override)
    return call_openai_image(prompt=prompt, image_bytes=image_bytes, cfg=cfg, model=model_override)


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
    dest_path = dest_dir / f"ai_{uuid.uuid4().hex[:8]}.jpg"

    with Image.open(io.BytesIO(generated_png_bytes)) as pil_img:
        if pil_img.mode in ("RGBA", "LA") or (pil_img.mode == "P" and "transparency" in pil_img.info):
            bg = Image.new("RGB", pil_img.size, (255, 255, 255))
            pil_rgba = pil_img.convert("RGBA")
            bg.paste(pil_rgba, mask=pil_rgba.split()[3])
            rgb_img = bg
        else:
            rgb_img = pil_img.convert("RGB")
        rgb_img.save(dest_path, format="JPEG", quality=95)
        width, height = rgb_img.size

    sha = _compute_sha256(dest_path)
    transform = list(parent_photo.transform_log or []) + [
        {"step": step_name, "prompt": prompt, "parent_id": parent_photo.id, "resulting_size": [width, height]}
    ]
    new_photo = ItemPhoto(
        id=str(uuid.uuid4()),
        item_code=parent_photo.item_code,
        role=parent_photo.role,
        path=str(dest_path),
        sha256=sha,
        width=width,
        height=height,
        derived_from_id=parent_photo.id,
        transform_log=transform,
        ai_generated=True,
        vlm_verdict=dict(parent_photo.vlm_verdict or {}),
    )
    session.add(new_photo)
    session.flush()

    item = session.get(Item, parent_photo.item_code)
    if item:
        attrs = dict(item.attrs or {})
        order = list(attrs.get("photo_order") or [])
        if parent_photo.id in order:
            order[order.index(parent_photo.id)] = new_photo.id
            attrs["photo_order"] = order
        if attrs.get("cover_photo_id") == parent_photo.id:
            attrs["cover_photo_id"] = new_photo.id
        item.attrs = attrs
    return new_photo


def _run_ai_photo_step(
    photo_id: str,
    session: Session,
    step_name: str,
    default_prompt: str,
    model_key: str,
    prompt: str | None = None,
    config: dict[str, str] | None = None,
) -> ItemPhoto:
    photo = session.get(ItemPhoto, photo_id)
    if not photo:
        raise ValueError(f"Фото {photo_id} не найдено")
    p_path = Path(photo.path)
    if not p_path.exists():
        raise FileNotFoundError(f"Файл фото {photo.path} не найден на диске")

    cfg = config or get_image_ai_config()
    target_prompt = (prompt or "").strip() or default_prompt
    target_model = cfg.get(model_key) or cfg.get("model")

    with open(p_path, "rb") as f:
        raw_bytes = f.read()

    edited_bytes = call_image_ai_edits(
        image_bytes=raw_bytes,
        prompt=target_prompt,
        config=cfg,
        model_override=target_model,
    )
    return _save_derived_ai_photo(session, photo, edited_bytes, step_name, target_prompt)


def ai_remove_dust(
    photo_id: str,
    session: Session,
    prompt: str | None = None,
    config: dict[str, str] | None = None,
) -> ItemPhoto:
    """Perform generative dust and scratch removal."""
    return _run_ai_photo_step(
        photo_id=photo_id,
        session=session,
        step_name="ai_remove_dust",
        default_prompt=PROMPT_REMOVE_DUST,
        model_key="model_dust",
        prompt=prompt,
        config=config,
    )


def ai_selling_image(
    photo_id: str,
    session: Session,
    prompt: str | None = None,
    config: dict[str, str] | None = None,
) -> ItemPhoto:
    """Generate clean neutral studio background with soft realistic shadow."""
    return _run_ai_photo_step(
        photo_id=photo_id,
        session=session,
        step_name="ai_selling_image",
        default_prompt=PROMPT_SELLING_IMAGE,
        model_key="model_selling",
        prompt=prompt,
        config=config,
    )


def ai_replace_bg(
    photo_id: str,
    session: Session,
    prompt: str | None = None,
    config: dict[str, str] | None = None,
) -> ItemPhoto:
    """Isolate subject and replace background with clean modern background."""
    return _run_ai_photo_step(
        photo_id=photo_id,
        session=session,
        step_name="ai_replace_bg",
        default_prompt=PROMPT_REPLACE_BG,
        model_key="model_bg",
        prompt=prompt,
        config=config,
    )


def undo_photo(session: Session, photo_id: str) -> ItemPhoto | None:
    """Revert photo to previous version in derived_from_id chain (§5.4)."""
    photo = session.get(ItemPhoto, photo_id)
    if not photo or not photo.derived_from_id:
        return None
    parent = session.get(ItemPhoto, photo.derived_from_id)
    if not parent:
        return None

    item = session.get(Item, photo.item_code)
    if item:
        attrs = dict(item.attrs or {})
        order = list(attrs.get("photo_order") or [])
        if photo.id in order:
            order[order.index(photo.id)] = parent.id
            attrs["photo_order"] = order
        if attrs.get("cover_photo_id") == photo.id:
            attrs["cover_photo_id"] = parent.id
        item.attrs = attrs

    p_path = Path(photo.path)
    session.delete(photo)
    session.flush()
    if p_path.exists():
        try:
            p_path.unlink()
        except OSError:
            pass
    return parent
