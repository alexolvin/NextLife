"""Original image ingestion, EXIF extraction/stripping, and hashing (TZ v2.0 Section IV.2)."""

import hashlib
import io
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image
from pydantic import BaseModel, Field

GPS_TAG_ID = 0x8825  # Standard EXIF GPSInfo tag


class IngestResult(BaseModel):
    sha256: str
    file_bytes: int
    original_path: str
    sanitized_path: str
    exif_data: dict[str, Any] = Field(default_factory=dict)
    orientation: int = 1
    width: int
    height: int


def extract_safe_exif(image: Image.Image) -> tuple[dict[str, Any], int]:
    """Extract EXIF data strictly purging any GPS coordinates or privacy-sensitive geolocation data.

    Returns:
        (sanitized_exif_dict, orientation)
    """
    raw_exif = image.getexif()
    if not raw_exif:
        return {}, 1

    safe_exif: dict[str, Any] = {}
    orientation = 1

    for tag_id, value in raw_exif.items():
        # DoD: GPS is NEVER saved under any circumstances!
        if tag_id == GPS_TAG_ID or "GPS" in str(ExifTags.TAGS.get(tag_id, "")):
            continue

        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))

        # Check for orientation tag
        if tag_id == 0x0112 or tag_name == "Orientation":
            try:
                orientation = int(value)
            except Exception:
                orientation = 1

        # Save string/numeric values into safe exif metadata
        if isinstance(value, (int, float, str)):
            safe_exif[tag_name] = value
        elif isinstance(value, bytes):
            try:
                safe_exif[tag_name] = value.decode("utf-8", errors="ignore").strip()
            except Exception:
                pass

    return safe_exif, orientation


def ingest_original_image(
    raw_bytes: bytes,
    storage_dir: Path | str,
    original_filename: str,
) -> IngestResult:
    """Ingest, hash, and store immutable original photo, and generate initial sanitized master.

    DoD:
    - Original file is preserved byte-for-byte identical.
    - EXIF is extracted to DB metadata; GPS is NEVER stored.
    - Clean copy completely strips all EXIF metadata for publication.
    """
    target_dir = Path(storage_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Compute SHA-256 of immutable original
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
    file_size = len(raw_bytes)

    # 2. Save immutable original byte-for-byte
    orig_path = target_dir / f"orig_{sha256_hash[:16]}_{original_filename}"
    orig_path.write_bytes(raw_bytes)

    # Byte-for-byte integrity check
    if orig_path.read_bytes() != raw_bytes:
        raise ValueError("Critical: Stored original file differs from uploaded raw bytes!")

    # 3. Read image for dimension and EXIF inspection
    with Image.open(io.BytesIO(raw_bytes)) as img:
        width, height = img.size
        img_format = img.format or "JPEG"
        safe_exif, orientation = extract_safe_exif(img)

        # 4. Generate clean sanitized version with EXIF stripped
        clean_img = img.copy()

    # Save sanitized file without EXIF
    sanitized_path = target_dir / f"clean_{sha256_hash[:16]}.{img_format.lower()}"
    clean_img.save(sanitized_path, format=img_format)

    # Verify sanitized copy has no EXIF
    with Image.open(sanitized_path) as check_img:
        check_exif = check_img.getexif()
        assert len(check_exif) == 0, "Sanitized image must not contain any EXIF tags!"

    return IngestResult(
        sha256=sha256_hash,
        file_bytes=file_size,
        original_path=str(orig_path),
        sanitized_path=str(sanitized_path),
        exif_data=safe_exif,
        orientation=orientation,
        width=width,
        height=height,
    )
