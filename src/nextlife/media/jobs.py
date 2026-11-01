"""Media variant generation background jobs and idempotency management (TZ v2.0 Section IV.2)."""

import hashlib
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nextlife.core.config import get_media_dir
from nextlife.media.models import MediaMaster, MediaOriginal, MediaVariant
from nextlife.media.pipeline import (
    deterministic_process,
    display_photo_for_chain,
    logical_photo_chains,
)
from nextlife.media.profiles import MediaProfile, load_marketplace_media_profile
from nextlife.media.variants import generate_media_variant
from nextlife.storage.models.core import Job


def compute_variant_job_key(master_id: str, marketplace_key: str, role: str, profile_hash: str) -> str:
    """Deterministic idempotency key for media variant generation."""
    raw = f"{master_id}:{marketplace_key}:{role}:{profile_hash}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"media_variant_{h[:24]}"


def enqueue_or_get_variant(
    session: Session,
    master_id: str,
    marketplace_key: str,
    role: str = "GALLERY",
    custom_profile: MediaProfile | None = None,
    custom_profile_hash: str | None = None,
    storage_dir: Path | str | None = None,
) -> tuple[MediaVariant | None, Job | None, bool]:
    """Ensure a variant exists or schedule its generation idempotently.

    Returns:
        (variant, job, created_new_job)

    DoD:
    - Repeat execution does not create duplicates.
    - Profile change marks older variants as stale and schedules a new generation task.
    """
    if storage_dir is None:
        storage_dir = get_media_dir() / "variants"
    if custom_profile and custom_profile_hash:
        profile = custom_profile
        profile_hash = custom_profile_hash
    else:
        profile, profile_hash = load_marketplace_media_profile(marketplace_key)

    idempotency_key = compute_variant_job_key(master_id, marketplace_key, role, profile_hash)

    # 1. Check if identical variant already exists
    stmt = select(MediaVariant).where(
        MediaVariant.master_id == master_id,
        MediaVariant.marketplace_key == marketplace_key,
        MediaVariant.role == role,
        MediaVariant.profile_hash == profile_hash,
    )
    existing_exact = session.execute(stmt).scalar_one_or_none()
    if existing_exact:
        # Already exists and up to date, no duplicates!
        return existing_exact, None, False

    # 2. Check if older variant exists with different profile_hash (profile changed!)
    stmt_old = select(MediaVariant).where(
        MediaVariant.master_id == master_id,
        MediaVariant.marketplace_key == marketplace_key,
        MediaVariant.role == role,
    )
    older_variants = session.execute(stmt_old).scalars().all()
    for old_v in older_variants:
        if old_v.profile_hash != profile_hash:
            # DoD: Mark old variants stale upon profile change
            old_v.stale = True

    # 3. Check if job already enqueued
    stmt_job = select(Job).where(Job.idempotency_key == idempotency_key)
    existing_job = session.execute(stmt_job).scalar_one_or_none()
    if existing_job:
        return None, existing_job, False

    # 4. Enqueue new background job
    job = Job(
        id=str(uuid.uuid4()),
        kind="GENERATE_MEDIA_VARIANT",
        payload={
            "master_id": master_id,
            "marketplace_key": marketplace_key,
            "role": role,
            "profile_hash": profile_hash,
            "storage_dir": str(storage_dir),
        },
        idempotency_key=idempotency_key,
        state="PENDING",
    )
    session.add(job)
    session.commit()

    return None, job, True


def execute_variant_job(session: Session, job: Job) -> MediaVariant:
    """Execute a media variant generation job synchronously."""
    payload = job.payload
    master_id = payload["master_id"]
    mp_key = payload["marketplace_key"]
    role = payload["role"]
    p_hash = payload["profile_hash"]
    out_dir = Path(payload.get("storage_dir") or get_media_dir() / "variants")

    master = session.execute(select(MediaMaster).where(MediaMaster.id == master_id)).scalar_one()
    profile, _ = load_marketplace_media_profile(mp_key)

    out_file = out_dir / f"var_{master_id}_{mp_key}_{role}.jpg"
    res = generate_media_variant(master.file_path, out_file, profile, role=role)

    variant = MediaVariant(
        master_id=master_id,
        marketplace_key=mp_key,
        role=role,
        profile_hash=p_hash,
        file_path=res.file_path,
        sha256=res.sha256,
        width=res.width,
        height=res.height,
        file_bytes=res.file_bytes,
        stale=False,
    )
    session.add(variant)
    job.state = "COMPLETED"
    session.commit()
    return variant


def sync_tier_models_and_generate_variants(
    session: Session,
    item_code: str,
    orig_photo: Any,
    proc_photo: Any,
    role: str = "ORIGINAL",
    storage_dir: Path | str | None = None,
) -> MediaMaster:
    if storage_dir is None:
        storage_dir = get_media_dir() / "variants"
    orig_path = Path(orig_photo.path)
    file_bytes = orig_path.stat().st_size if orig_path.exists() else 0

    orig = MediaOriginal(
        item_code=item_code,
        file_path=str(orig_photo.path),
        sha256=orig_photo.sha256,
        file_bytes=file_bytes,
        exif_data={},
    )
    session.add(orig)
    session.flush()

    is_cover = bool(role == "COVER" or getattr(orig_photo, "role", "") == "COVER")
    master = MediaMaster(
        original_id=orig.id,
        file_path=str(proc_photo.path),
        sha256=proc_photo.sha256,
        width=proc_photo.width,
        height=proc_photo.height,
        orientation=1,
        is_cover=is_cover,
    )
    session.add(master)
    session.flush()

    marketplaces = ["bazos_cz", "sbazar_cz", "aukro_cz"]
    cfg_dir = Path("config/marketplaces")
    if cfg_dir.exists():
        found = [p.stem for p in cfg_dir.glob("*.yaml")]
        if found:
            marketplaces = sorted(found)

    variant_role = "COVER" if is_cover else "GALLERY"
    for mp_key in marketplaces:
        _, job, _ = enqueue_or_get_variant(
            session=session,
            master_id=master.id,
            marketplace_key=mp_key,
            role=variant_role,
            storage_dir=storage_dir,
        )
        if job:
            execute_variant_job(session, job)

    return master


def sync_item_media(session: Session, item_code: str) -> int:
    """TZ-NextLife-UI-N2 §4 V3: согласовать конвейер медиа с цепочками item_photo.

    1. Цепочка с обработанной версией, но без публикуемой (PUBLISH),
       доводится до PUBLISH (публикуемая версия — главная, P-E04b).
    2. Каждая цепочка (включая посевные фото) регистрируется в трёхуровневом
       конвейере: MediaOriginal от оригинала, MediaMaster от публикуемой
       версии цепочки (превью соответствует факту, P-E08).
    Идемпотентно: ключ (item_code, sha256 оригинала); существующие уровни не
    дублируются. Возвращает число цепочек, добавленных в конвейер.
    """
    for chain in logical_photo_chains(session, item_code):
        roles = {p.role for p in chain}
        # TZ-NextLife-UI-N2 §4 V6: явная пометка «фото дефекта» стоит на
        # производной версии (не на корне цепочки) — не регенерировать
        # публикуемую версию поверх пометки. Посевные фото дефекта
        # (DEFECT на корне) продолжают доводиться до PUBLISH, как в V3.
        defect_marked = any(p.role == "DEFECT" and p is not chain[0] for p in chain)
        if "PUBLISH" in roles or "PROCESSED" not in roles or defect_marked:
            continue
        source = next(p for p in chain if p.role == "PROCESSED")
        try:
            deterministic_process(session=session, photo=source)
        except Exception:  # noqa: BLE001
            # Битый/недоступный файл: цепочка остаётся без публикуемой версии,
            # экран показывает имеющуюся с пометкой битого файла (P-E05).
            continue

    created = 0
    for chain in logical_photo_chains(session, item_code):
        root = chain[0]
        display = display_photo_for_chain(chain)
        # scalars().first(): в production для лота 130 есть дубли (item_code,
        # sha256) в media_original — scalar_one_or_none() падал
        # MultipleResultsFound при рендере карточки. first() устойчив к дублям.
        existing = session.execute(
            select(MediaOriginal).where(
                MediaOriginal.item_code == item_code,
                MediaOriginal.sha256 == root.sha256,
            )
        ).scalars().first()
        if existing is not None:
            continue
        orig_path = Path(root.path)
        orig = MediaOriginal(
            item_code=item_code,
            file_path=str(root.path),
            sha256=root.sha256,
            file_bytes=orig_path.stat().st_size if orig_path.exists() else 0,
            exif_data={},
        )
        session.add(orig)
        session.flush()
        master = MediaMaster(
            original_id=orig.id,
            file_path=str(display.path),
            sha256=display.sha256,
            width=display.width,
            height=display.height,
            orientation=1,
            is_cover=False,
        )
        session.add(master)
        session.flush()
        created += 1
    return created


def regenerate_item_variants(session: Session, item_code: str) -> int:
    """TZ-NextLife-UI-N2 §4 V6: догенерация вариантов под каждую площадку.

    Файлы мастеров могут измениться пер-фото операциями (поворот,
    обрезка, очистка), поэтому варианты всех площадок собираются заново
    из актуальных файлов. Идемпотентность job-очереди здесь не
    используется: старые варианты удаляются, генерация — прямая.
    Возвращает число пересобранных вариантов.
    """
    masters = session.execute(
        select(MediaMaster)
        .join(MediaOriginal, MediaMaster.original_id == MediaOriginal.id)
        .where(MediaOriginal.item_code == item_code)
    ).scalars().all()

    cfg_dir = Path("config/marketplaces")
    marketplaces = (
        sorted(p.stem for p in cfg_dir.glob("*.yaml"))
        if cfg_dir.exists()
        else ["bazos_cz", "sbazar_cz", "aukro_cz"]
    )
    out_dir = get_media_dir() / "variants"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Мастер следует за текущей публикуемой версией цепочки (P-E08):
    # пер-фото операции меняют файл публикуемой версии, и варианты
    # пересобираются именно из него.
    chains = {
        c[0].sha256: c for c in logical_photo_chains(session, item_code)
    }
    count = 0
    for master in masters:
        orig = session.get(MediaOriginal, master.original_id)
        chain = chains.get(orig.sha256) if orig else None
        if chain is not None:
            display = display_photo_for_chain(chain)
            master.file_path = str(display.path)
            master.sha256 = display.sha256
            master.width = display.width
            master.height = display.height
        existing = session.execute(
            select(MediaVariant).where(MediaVariant.master_id == master.id)
        ).scalars().all()
        roles = {v.marketplace_key: v.role for v in existing}
        for old in existing:
            session.delete(old)
        session.flush()
        for mp_key in marketplaces:
            role = roles.get(mp_key) or ("COVER" if master.is_cover else "GALLERY")
            profile, profile_hash = load_marketplace_media_profile(mp_key)
            out_file = out_dir / f"var_{master.id}_{mp_key}_{role}.jpg"
            res = generate_media_variant(master.file_path, out_file, profile, role=role)
            session.add(MediaVariant(
                master_id=master.id,
                marketplace_key=mp_key,
                role=role,
                profile_hash=profile_hash,
                file_path=res.file_path,
                sha256=res.sha256,
                width=res.width,
                height=res.height,
                file_bytes=res.file_bytes,
                stale=False,
            ))
            count += 1
    session.flush()
    return count

