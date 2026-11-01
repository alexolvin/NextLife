"""Marketplace variant image generation pipeline (TZ v2.0 Section IV.2)."""

import hashlib
import io
from pathlib import Path

from PIL import Image
from pydantic import BaseModel

from nextlife.core.exceptions import NextLifeError
from nextlife.media.profiles import MediaProfile


class VariantGenerationError(NextLifeError):
    """Raised when variant cannot be produced within marketplace profile limits."""


class VariantResult(BaseModel):
    file_path: str
    sha256: str
    width: int
    height: int
    file_bytes: int
    marketplace_key: str
    role: str


def crop_to_aspect_ratio(img: Image.Image, aspect_str: str = "4:3") -> Image.Image:
    """Center crop image to match desired aspect ratio (e.g. '4:3', '16:9', '1:1')."""
    try:
        parts = aspect_str.split(":")
        target_ratio = float(parts[0]) / float(parts[1])
    except Exception:
        target_ratio = 4.0 / 3.0

    cur_w, cur_h = img.size
    cur_ratio = cur_w / cur_h

    if abs(cur_ratio - target_ratio) < 0.01:
        return img.copy()

    if cur_ratio > target_ratio:
        # Too wide, crop width
        new_w = int(cur_h * target_ratio)
        offset = (cur_w - new_w) // 2
        return img.crop((offset, 0, offset + new_w, cur_h))
    else:
        # Too tall, crop height
        new_h = int(cur_w / target_ratio)
        offset = (cur_h - new_h) // 2
        return img.crop((0, offset, cur_w, offset + new_h))


def generate_media_variant(
    master_path: Path | str,
    output_path: Path | str,
    profile: MediaProfile,
    role: str = "GALLERY",
) -> VariantResult:
    """Generate platform-specific VARIANT from MASTER.

    DoD:
    - Strictly conforms to profile dimensions, aspect ratio, and max_file_bytes limits.
    - If limit is unreachable, raises VariantGenerationError; never outputs oversized file.
    """
    master = Path(master_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not master.exists():
        raise VariantGenerationError(f"Master image not found: {master}")

    with Image.open(master) as raw_img:
        # 1. Convert to RGB for universal compatibility
        rgb_img = raw_img.convert("RGB") if raw_img.mode != "RGB" else raw_img

        # 2. Crop to preferred aspect ratio
        cropped = crop_to_aspect_ratio(rgb_img, profile.aspect_preferred)

        # 3. Scale down to fit max_width and max_height if necessary
        target_w = min(cropped.width, profile.max_width)
        target_h = min(cropped.height, profile.max_height)

        # Preserve aspect ratio while scaling
        ratio = min(target_w / cropped.width, target_h / cropped.height)
        final_w = int(cropped.width * ratio)
        final_h = int(cropped.height * ratio)

        # Ensure not below min dimensions if possible
        if final_w < profile.min_width and cropped.width >= profile.min_width:
            final_w = profile.min_width
        if final_h < profile.min_height and cropped.height >= profile.min_height:
            final_h = profile.min_height

        scaled = cropped.resize((final_w, final_h), resample=Image.Resampling.LANCZOS)

        # 4. Format selection
        fmt = "JPEG"
        if profile.formats and "JPEG" in [f.upper() for f in profile.formats]:
            fmt = "JPEG"
        elif profile.formats:
            fmt = profile.formats[0].upper()

        # 5. Compression loop under profile.max_file_bytes
        quality = 95
        saved_bytes = None

        while quality >= 25:
            buf = io.BytesIO()
            scaled.save(buf, format=fmt, quality=quality, optimize=True)
            candidate_bytes = buf.getvalue()
            if len(candidate_bytes) <= profile.max_file_bytes:
                saved_bytes = candidate_bytes
                break
            quality -= 10

        # If still exceeding, try downscaling resolution
        if saved_bytes is None:
            cur_scale = scaled.copy()
            while cur_scale.width > profile.min_width:
                cur_scale = cur_scale.resize(
                    (int(cur_scale.width * 0.8), int(cur_scale.height * 0.8)),
                    resample=Image.Resampling.LANCZOS,
                )
                buf = io.BytesIO()
                cur_scale.save(buf, format=fmt, quality=35, optimize=True)
                candidate_bytes = buf.getvalue()
                if len(candidate_bytes) <= profile.max_file_bytes:
                    saved_bytes = candidate_bytes
                    scaled = cur_scale
                    break

        # DoD: If limit unreachable, strictly raise error instead of returning oversized file
        if saved_bytes is None:
            raise VariantGenerationError(
                f"Cannot compress variant for marketplace '{profile.marketplace_key}' "
                f"within limit of {profile.max_file_bytes} bytes. Lowest reached was {len(candidate_bytes)} bytes."
            )

        out.write_bytes(saved_bytes)

    file_hash = hashlib.sha256(saved_bytes).hexdigest()

    return VariantResult(
        file_path=str(out),
        sha256=file_hash,
        width=scaled.width,
        height=scaled.height,
        file_bytes=len(saved_bytes),
        marketplace_key=profile.marketplace_key,
        role=role,
    )
