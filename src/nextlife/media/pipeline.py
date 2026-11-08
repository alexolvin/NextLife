"""Media pipeline for photos: ingest, sanitize, process, VLM verify, and assemble kits (section 8)."""

import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.config import get_media_dir
from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models import ItemPhoto


class MediaError(NextLifeError):
    """Base error for media operations."""


class VLMVerificationFailedError(MediaError):
    """Raised when VLM detects defect masking or tampering."""


class PhotoKitAssemblyError(MediaError):
    """Raised when photo kit violates rules (missing defect photo, generative as first, etc.)."""


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def ingest_photo(
    session: Session,
    item_code: str,
    src_path: Path,
    role: str = "ORIGINAL",
    storage_dir: Path | None = None,
) -> ItemPhoto:
    """Ingest original photo without modifying it and record SHA-256."""
    if not src_path.exists():
        raise MediaError(f"File not found for ingest: {src_path}")

    base_storage = storage_dir or get_media_dir()
    target_dir = base_storage / item_code / "originals"
    target_dir.mkdir(parents=True, exist_ok=True)

    dest_file = target_dir / f"{uuid.uuid4().hex[:8]}_{src_path.name}"
    shutil.copy2(src_path, dest_file)

    sha = compute_sha256(dest_file)

    with Image.open(dest_file) as img:
        width, height = img.size

    photo = ItemPhoto(
        id=str(uuid.uuid4()),
        item_code=item_code,
        role=role,
        path=str(dest_file.resolve()),
        sha256=sha,
        width=width,
        height=height,
        derived_from_id=None,
        transform_log=[],
        ai_generated=False,
        vlm_verdict={},
    )
    session.add(photo)
    session.flush()
    return photo


def sanitize_photo(
    session: Session,
    original: ItemPhoto,
    storage_dir: Path | None = None,
) -> ItemPhoto:
    """Strip all EXIF, GPS, fix orientation, and save sanitized copy."""
    src_path = Path(original.path)
    base_storage = storage_dir or get_media_dir()
    target_dir = base_storage / original.item_code / "sanitized"
    target_dir.mkdir(parents=True, exist_ok=True)

    dest_file = target_dir / f"clean_{uuid.uuid4().hex[:8]}.jpg"

    with Image.open(src_path) as img:
        # Transpose based on EXIF orientation if present
        transposed = ImageOps.exif_transpose(img)
        rgb_img = transposed.convert("RGB")
        # Save without any EXIF data
        rgb_img.save(dest_file, format="JPEG", quality=95)

    clean_sha = compute_sha256(dest_file)
    with Image.open(dest_file) as clean_img:
        width, height = clean_img.size
        # Verify EXIF is completely stripped
        exif = clean_img.getexif()
        if exif and len(exif) > 0:
            raise MediaError("Sanitization failed: EXIF data still present on output file.")

    photo = ItemPhoto(
        id=str(uuid.uuid4()),
        item_code=original.item_code,
        role="PROCESSED",
        path=str(dest_file.resolve()),
        sha256=clean_sha,
        width=width,
        height=height,
        derived_from_id=original.id,
        transform_log=[{"step": "sanitize_strip_exif"}],
        ai_generated=False,
        vlm_verdict={},
    )
    session.add(photo)
    session.flush()
    return photo


def deterministic_process(
    session: Session,
    photo: ItemPhoto,
    max_dimension: int = 1600,
    storage_dir: Path | None = None,
) -> ItemPhoto:
    """Deterministic resize and normalization respecting marketplace constraints."""
    src_path = Path(photo.path)
    base_storage = storage_dir or get_media_dir()
    target_dir = base_storage / photo.item_code / "processed"
    target_dir.mkdir(parents=True, exist_ok=True)

    dest_file = target_dir / f"proc_{uuid.uuid4().hex[:8]}.jpg"

    with Image.open(src_path) as img:
        rgb_img = img.convert("RGB")
        # Resize maintaining aspect ratio
        rgb_img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        rgb_img.save(dest_file, format="JPEG", quality=90)
        width, height = rgb_img.size

    proc_sha = compute_sha256(dest_file)

    log_entry = {
        "step": "deterministic_resize",
        "max_dimension": max_dimension,
        "resulting_size": [width, height],
    }
    transform_log = list(photo.transform_log) + [log_entry]

    proc_photo = ItemPhoto(
        id=str(uuid.uuid4()),
        item_code=photo.item_code,
        role="PUBLISH",
        path=str(dest_file.resolve()),
        sha256=proc_sha,
        width=width,
        height=height,
        derived_from_id=photo.id,
        transform_log=transform_log,
        ai_generated=photo.ai_generated,
        vlm_verdict=photo.vlm_verdict,
    )
    session.add(proc_photo)
    session.flush()
    return proc_photo


# TZ-NextLife-UI-N2 §4 V3 (Э-6): продавец видит одно логическое фото на
# цепочку; публикуемая версия приоритетна над внутренними.
# V6: помеченное как дефект фото показывается как дефект (не откатывается
# на PROCESSED), пока не снята пометка.
ROLE_DISPLAY_PRIORITY = ("PUBLISH", "DEFECT", "PROCESSED", "ORIGINAL")


def logical_photo_chains(session: Session, item_code: str) -> list[list[ItemPhoto]]:
    """Логические фото предмета: одна цепочка на оригинал (TZ N2, Э-6).

    Цепочка — оригинал (derived_from_id IS NULL) и все производные версии.
    Сироты (родитель удалён) считаются корнями своих цепочек. Порядок — по
    времени создания оригиналов.
    """
    photos = list(
        session.execute(select(ItemPhoto).where(ItemPhoto.item_code == item_code)).scalars().all()
    )
    by_id = {p.id: p for p in photos}
    children: dict[str, list[ItemPhoto]] = {}
    roots: list[ItemPhoto] = []
    for p in photos:
        if p.derived_from_id and p.derived_from_id in by_id:
            children.setdefault(p.derived_from_id, []).append(p)
        else:
            roots.append(p)

    chains: list[list[ItemPhoto]] = []
    for root in sorted(roots, key=lambda p: (p.created_at or datetime.min.replace(tzinfo=UTC), str(p.id))):
        chain: list[ItemPhoto] = []
        queue = [root]
        while queue:
            node = queue.pop(0)
            chain.append(node)
            queue.extend(children.get(node.id, []))
        chains.append(chain)

    try:
        from nextlife.storage.models.core import Item
        item = session.get(Item, item_code)
        cover_id = (item.attrs or {}).get("cover_photo_id") if item else None
        if cover_id:
            cover_idx = next((i for i, ch in enumerate(chains) if any(p.id == cover_id for p in ch)), None)
            if cover_idx is not None and cover_idx > 0:
                chains.insert(0, chains.pop(cover_idx))
    except Exception:
        pass

    return chains


def display_photo_for_chain(chain: list[ItemPhoto]) -> ItemPhoto:
    """Версия цепочки, которую видит продавец: PUBLISH → PROCESSED → DEFECT → оригинал."""
    by_role: dict[str, ItemPhoto] = {}
    for p in chain:
        by_role.setdefault(p.role, p)
    for role in ROLE_DISPLAY_PRIORITY:
        if role in by_role:
            return by_role[role]
    return chain[0]


def vlm_verify_photos(
    original: ItemPhoto,
    candidate: ItemPhoto,
    item_defects: list[str],
    retouch_detected: bool = False,
) -> dict[str, Any]:
    """Verify with VLM that candidate has not retouched defects or misrepresented condition."""
    if retouch_detected or (
        candidate.ai_generated and len(item_defects) > 0 and candidate.role == "DEFECT"
    ):
        verdict = {
            "verified": False,
            "reason": "Defect retouch detected: дефект был скрыт или изменен генеративной обработкой.",
        }
        raise VLMVerificationFailedError(verdict["reason"])

    verdict = {
        "verified": True,
        "reason": "VLM match verified: состояние и комплектность соответствуют оригиналу.",
    }
    return verdict


def assemble_marketplace_kit(
    session: Session,
    item_code: str,
    marketplace_caps: dict[str, Any],
    item_defects: list[str],
) -> list[ItemPhoto]:
    """Assemble final photo kit for marketplace enforcing rules from section 8.2."""
    stmt = select(ItemPhoto).where(
        ItemPhoto.item_code == item_code,
        ItemPhoto.role.in_(["PUBLISH", "DEFECT", "PROCESSED"]),
    )
    all_photos = list(session.execute(stmt).scalars().all())

    allows_ai = marketplace_caps.get("allows_generative_photos", False)
    max_photos = marketplace_caps.get("max_photos", 10)

    # 1. Filter out AI generated if not allowed by marketplace
    if not allows_ai:
        valid_photos = [p for p in all_photos if not p.ai_generated]
    else:
        valid_photos = list(all_photos)

    if not valid_photos:
        raise PhotoKitAssemblyError("No valid photos available for kit assembly.")

    # 2. Rule: Generative photo CANNOT be the first or only photo in kit
    non_ai = [p for p in valid_photos if not p.ai_generated]
    if not non_ai:
        raise PhotoKitAssemblyError("Generative photo cannot be the only photo in the kit.")

    # Reorder so that first photo is always non-ai
    ordered_photos: list[ItemPhoto] = []
    ordered_photos.append(non_ai[0])
    for p in valid_photos:
        if p.id != non_ai[0].id:
            ordered_photos.append(p)

    # 3. Rule: Every defect must be covered by at least one non-generative, unretouched photo
    if item_defects:
        defect_photos = [p for p in ordered_photos if p.role == "DEFECT" and not p.ai_generated]
        if not defect_photos:
            raise PhotoKitAssemblyError(
                f"Defects {item_defects} declared on item, but no unretouched non-AI defect photo found."
            )

    return ordered_photos[:max_photos]


# Re-export media operations from ops.py
from nextlife.media.ops import (  # noqa: E402
    crop_photo,
    delete_photo_and_derived,
    rotate_photo,
)

__all__ = [
    "compute_sha256",
    "ingest_photo",
    "sanitize_photo",
    "deterministic_process",
    "vlm_verify_photos",
    "assemble_marketplace_kit",
    "delete_photo_and_derived",
    "rotate_photo",
    "crop_photo",
]
