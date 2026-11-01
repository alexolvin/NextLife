"""Master image generation and normalization pipeline (TZ v2.0 Section IV.2)."""

import hashlib
from pathlib import Path

from PIL import Image, ImageOps
from pydantic import BaseModel


class MasterResult(BaseModel):
    file_path: str
    sha256: str
    width: int
    height: int
    file_bytes: int


def generate_master_image(
    original_path: Path | str,
    output_path: Path | str,
    crop_box: tuple[int, int, int, int] | None = None,
    rotation_angle_deg: float = 0.0,
    normalize_contrast: bool = False,
) -> MasterResult:
    """Generate normalized MASTER image from ORIGINAL.

    DoD:
    - Normalizes orientation, performs crop and alignment.
    - Deterministic: Re-running with same parameters yields byte-for-byte identical output.
    """
    orig = Path(original_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(orig) as img:
        # 1. Normalize orientation from EXIF
        processed = ImageOps.exif_transpose(img) or img.copy()

        # Convert to RGB if needed (e.g. RGBA/P to standard RGB for master)
        if processed.mode not in ("RGB", "L"):
            processed = processed.convert("RGB")

        # 2. Fine rotation/leveling if specified
        if rotation_angle_deg != 0.0:
            processed = processed.rotate(
                rotation_angle_deg,
                resample=Image.Resampling.BICUBIC,
                expand=False,
            )

        # 3. Crop box if specified: (left, top, right, bottom)
        if crop_box is not None:
            processed = processed.crop(crop_box)

        # 4. Basic color/contrast normalization
        if normalize_contrast:
            processed = ImageOps.autocontrast(processed, cutoff=1)

        # 5. Deterministic saving: PNG or uncompressed deterministic JPEG
        # PNG provides strictly deterministic, lossless encoding
        processed.save(out, format="PNG", optimize=False)

    out_bytes = out.read_bytes()
    file_hash = hashlib.sha256(out_bytes).hexdigest()

    return MasterResult(
        file_path=str(out),
        sha256=file_hash,
        width=processed.width,
        height=processed.height,
        file_bytes=len(out_bytes),
    )
