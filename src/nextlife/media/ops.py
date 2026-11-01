"""Media manipulation operations: delete, rotate, crop, clean (TZ v2.0 IV.2, N2 §4 V6).

Guarantees Tier 1 immutability: ORIGINAL images are never mutated on disk.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.storage.models import ItemPhoto


class PhotoOpForbiddenError(ValueError):
    """TZ-NextLife-UI-N2 §4 V6: ретушь фото дефекта запрещена (честность)."""


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def delete_photo_and_derived(session: Session, code: str, photo_id: str) -> list[str]:
    """Delete a photo and all derived versions; return the file paths.

    The caller must commit and only then remove the returned files from
    disk: unlinking before commit leaves DB rows without files when the
    transaction rolls back (error 3f68648f215e — owner originals were
    lost on a failed delete). Empty list = photo not found / other item.
    """
    photo = session.get(ItemPhoto, photo_id)
    if not photo or photo.item_code != code:
        return []

    # Всё поддерево уровнями (root → ... → листья). Удаление с листьев и
    # flush на каждой строке: derived_from_id — self-FK NO ACTION, а без
    # явного flush SQLAlchemy батчит DELETE в порядке первичных ключей
    # (UUID), где родитель может встать раньше ребёнка. Одноуровневое
    # удаление падало на цепочках глубиной > 2 (ошибка 3f68648f215e,
    # 2026-09-11: ORIGINAL → PROCESSED → PUBLISH).
    levels: list[list[ItemPhoto]] = [[photo]]
    seen = {photo.id}
    frontier = [photo]
    while frontier:
        rows = (
            session.execute(
                select(ItemPhoto).where(
                    ItemPhoto.derived_from_id.in_([p.id for p in frontier])
                )
            )
            .scalars()
            .all()
        )
        rows = [r for r in rows if r.id not in seen]
        if not rows:
            break
        seen.update(r.id for r in rows)
        levels.append(rows)
        frontier = rows

    files: list[str] = []
    for level in reversed(levels):
        for p in level:
            files.append(p.path)
            session.delete(p)
            session.flush()
    return files


def _get_target_and_processed(
    session: Session, item_code: str, photo_id: str | None
) -> ItemPhoto | None:
    """Select the target photo and ensure we operate on a mutable processed derivative."""
    target: ItemPhoto | None = None
    if photo_id:
        target = session.get(ItemPhoto, photo_id)
        if not target or target.item_code != item_code:
            return None
    else:
        target = (
            session.execute(
                select(ItemPhoto)
                .where(
                    ItemPhoto.item_code == item_code,
                    ItemPhoto.role.in_(["PROCESSED", "PUBLISH", "ORIGINAL"]),
                )
                .order_by(ItemPhoto.created_at.desc())
            )
            .scalars()
            .first()
        )
        if not target:
            return None

    if target.role == "ORIGINAL":
        proc = (
            session.execute(
                select(ItemPhoto).where(
                    ItemPhoto.derived_from_id == target.id,
                    ItemPhoto.role == "PROCESSED",
                )
            )
            .scalars()
            .first()
        )
        if not proc:
            from nextlife.media.pipeline import sanitize_photo
            proc = sanitize_photo(session=session, original=target)
        return proc
    return target


def _regenerate_publish_derivatives(session: Session, photo: ItemPhoto, log_entry: dict[str, Any]) -> None:
    """Regenerate published thumbnail derivatives after an edit."""
    img_path = Path(photo.path)
    derived_publish = (
        session.execute(
            select(ItemPhoto).where(
                ItemPhoto.derived_from_id == photo.id,
                ItemPhoto.role == "PUBLISH",
            )
        )
        .scalars()
        .all()
    )
    for pub in derived_publish:
        p_path = Path(pub.path)
        with Image.open(img_path) as src_img:
            src_rgb = src_img.convert("RGB")
            src_rgb.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            src_rgb.save(p_path, format="JPEG", quality=90)
            pub.width, pub.height = src_rgb.size
            pub.sha256 = _compute_sha256(p_path)
            if isinstance(pub.transform_log, list):
                pub.transform_log = list(pub.transform_log) + [log_entry]


def rotate_photo(
    session: Session,
    item_code: str,
    photo_id: str | None = None,
    degrees: int = 90,
) -> ItemPhoto | None:
    """Rotate image by specified degrees (e.g. 90 deg clockwise)."""
    photo_to_rotate = _get_target_and_processed(session, item_code, photo_id)
    if not photo_to_rotate:
        return None

    img_path = Path(photo_to_rotate.path)
    if not img_path.exists():
        return None

    with Image.open(img_path) as img:
        rotated = img.rotate(-degrees, expand=True)
        rotated.convert("RGB").save(img_path, format="JPEG", quality=95)
        new_width, new_height = rotated.size

    photo_to_rotate.width = new_width
    photo_to_rotate.height = new_height
    photo_to_rotate.sha256 = _compute_sha256(img_path)

    log_entry = {
        "step": "rotate",
        "degrees": degrees,
        "resulting_size": [new_width, new_height],
    }
    if isinstance(photo_to_rotate.transform_log, list):
        photo_to_rotate.transform_log = list(photo_to_rotate.transform_log) + [log_entry]
    elif isinstance(photo_to_rotate.transform_log, dict):
        t_log = dict(photo_to_rotate.transform_log)
        t_log.update(log_entry)
        photo_to_rotate.transform_log = t_log
    else:
        photo_to_rotate.transform_log = [log_entry]

    _regenerate_publish_derivatives(session, photo_to_rotate, log_entry)
    session.flush()
    return photo_to_rotate


def crop_photo(
    session: Session,
    item_code: str,
    box: list[int],
    photo_id: str | None = None,
) -> ItemPhoto | None:
    """Crop image to specified [left, top, right, bottom] bounding box."""
    if len(box) != 4:
        return None

    photo_to_crop = _get_target_and_processed(session, item_code, photo_id)
    if not photo_to_crop:
        return None

    img_path = Path(photo_to_crop.path)
    if not img_path.exists():
        return None

    with Image.open(img_path) as img:
        left = max(0, min(box[0], img.width))
        top = max(0, min(box[1], img.height))
        right = max(left + 1, min(box[2], img.width))
        bottom = max(top + 1, min(box[3], img.height))

        cropped = img.crop((left, top, right, bottom))
        cropped.convert("RGB").save(img_path, format="JPEG", quality=95)
        new_width, new_height = cropped.size

    photo_to_crop.width = new_width
    photo_to_crop.height = new_height
    photo_to_crop.sha256 = _compute_sha256(img_path)

    log_entry = {
        "step": "crop",
        "box": [left, top, right, bottom],
        "resulting_size": [new_width, new_height],
    }
    if isinstance(photo_to_crop.transform_log, list):
        photo_to_crop.transform_log = list(photo_to_crop.transform_log) + [log_entry]
    elif isinstance(photo_to_crop.transform_log, dict):
        t_log = dict(photo_to_crop.transform_log)
        t_log.update(log_entry)
        photo_to_crop.transform_log = t_log
    else:
        photo_to_crop.transform_log = [log_entry]

    _regenerate_publish_derivatives(session, photo_to_crop, log_entry)
    session.flush()
    return photo_to_crop


def _apply_inplace_filter(
    session: Session,
    item_code: str,
    photo_id: str | None,
    transform: Callable[[Image.Image], Image.Image],
    step_name: str,
    forbidden_for_defect: bool = False,
) -> ItemPhoto | None:
    """Apply a size-preserving filter to a mutable version (TZ N2 §4 V6).

    ORIGINAL (Tier 1) never touched: the target is a processed derivative.
    """
    target = _get_target_and_processed(session, item_code, photo_id)
    if not target:
        return None

    if forbidden_for_defect and target.role == "DEFECT":
        raise PhotoOpForbiddenError(
            "Очистка запрещена для фото дефекта: заявленный дефект должен "
            "остаться представленным неретушированным снимком."
        )

    img_path = Path(target.path)
    if not img_path.exists():
        return None

    with Image.open(img_path) as img:
        result = transform(img)
        result.convert("RGB").save(img_path, format="JPEG", quality=95)
        new_width, new_height = result.size

    target.width = new_width
    target.height = new_height
    target.sha256 = _compute_sha256(img_path)

    log_entry = {"step": step_name, "resulting_size": [new_width, new_height]}
    if isinstance(target.transform_log, list):
        target.transform_log = list(target.transform_log) + [log_entry]
    elif isinstance(target.transform_log, dict):
        t_log = dict(target.transform_log)
        t_log.update(log_entry)
        target.transform_log = t_log
    else:
        target.transform_log = [log_entry]

    _regenerate_publish_derivatives(session, target, log_entry)
    session.flush()
    return target


def remove_dust_photo(
    session: Session, item_code: str, photo_id: str | None = None
) -> ItemPhoto | None:
    """Убрать пыль/засветы: медианный фильтр (TZ N2 §4 V6, R5)."""
    return _apply_inplace_filter(
        session, item_code, photo_id,
        lambda img: img.filter(ImageFilter.MedianFilter(3)),
        "remove_dust", forbidden_for_defect=True,
    )


def clean_background_photo(
    session: Session, item_code: str, photo_id: str | None = None
) -> ItemPhoto | None:
    """Очистить фон: нормализация контраста (TZ N2 §4 V6, R5)."""
    return _apply_inplace_filter(
        session, item_code, photo_id,
        ImageOps.autocontrast,
        "clean_background", forbidden_for_defect=True,
    )


def toggle_defect_role(
    session: Session, item_code: str, photo_id: str
) -> ItemPhoto | None:
    """Пометка «фото дефекта»: переключает роль DEFECT <-> PUBLISH (TZ N2 §4 V6)."""
    photo = session.get(ItemPhoto, photo_id)
    if not photo or photo.item_code != item_code:
        return None

    new_role = "PUBLISH" if photo.role == "DEFECT" else "DEFECT"
    photo.role = new_role
    log_entry = {"step": "defect_toggle", "role": new_role}
    if isinstance(photo.transform_log, list):
        photo.transform_log = list(photo.transform_log) + [log_entry]
    elif isinstance(photo.transform_log, dict):
        t_log = dict(photo.transform_log)
        t_log.update(log_entry)
        photo.transform_log = t_log
    else:
        photo.transform_log = [log_entry]
    session.flush()
    return photo
