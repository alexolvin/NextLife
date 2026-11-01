"""Tests for Stage E4 (Медиа-конвейер)."""

from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.orm import Session
from tests.conftest import clean_database

from nextlife.media.pipeline import (
    PhotoKitAssemblyError,
    VLMVerificationFailedError,
    assemble_marketplace_kit,
    compute_sha256,
    deterministic_process,
    ingest_photo,
    sanitize_photo,
    vlm_verify_photos,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item


def create_test_item(session: Session, code: str) -> Item:
    item = Item(
        code=code,
        kind="SELL",
        title=f"Test Item {code}",
        category_key="electronics",
        attrs={},
        condition_key="used",
        defects={},
        status="DRAFT",
        target_price=Decimal("50.00"),
        target_currency="EUR",
    )
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def clean_db_session() -> Session:
    with get_db_session() as session:
        clean_database(session)
        yield session
        clean_database(session)


@pytest.fixture
def sample_image_with_exif(tmp_path: Path) -> Path:
    img_path = tmp_path / "test_with_exif.jpg"
    img = Image.new("RGB", (800, 600), color=(200, 100, 50))
    # Add minimal EXIF data
    exif = img.getexif()
    exif[0x0112] = 1  # Orientation normal
    exif[0x010E] = "Test photo with simulated EXIF"
    img.save(img_path, format="JPEG", exif=exif)
    return img_path


def test_media_pipeline_ingest_and_sha256_preservation(
    clean_db_session: Session, sample_image_with_exif: Path, tmp_path: Path
) -> None:
    session = clean_db_session
    item_code = "777"
    create_test_item(session, item_code)
    storage_dir = tmp_path / "media_storage"

    # 1. Ingest original
    original_photo = ingest_photo(
        session=session,
        item_code=item_code,
        src_path=sample_image_with_exif,
        role="ORIGINAL",
        storage_dir=storage_dir,
    )

    orig_sha = compute_sha256(sample_image_with_exif)
    assert original_photo.sha256 == orig_sha
    assert original_photo.role == "ORIGINAL"
    assert Path(original_photo.path).exists()
    assert compute_sha256(Path(original_photo.path)) == orig_sha


def test_media_sanitization_removes_all_exif(
    clean_db_session: Session, sample_image_with_exif: Path, tmp_path: Path
) -> None:
    session = clean_db_session
    item_code = "777"
    create_test_item(session, item_code)
    storage_dir = tmp_path / "media_storage"

    # Verify input has EXIF
    with Image.open(sample_image_with_exif) as img:
        assert len(img.getexif()) > 0

    original_photo = ingest_photo(
        session=session,
        item_code=item_code,
        src_path=sample_image_with_exif,
        role="ORIGINAL",
        storage_dir=storage_dir,
    )

    # 2. Sanitize photo
    sanitized_photo = sanitize_photo(
        session=session, original=original_photo, storage_dir=storage_dir
    )

    assert sanitized_photo.derived_from_id == original_photo.id
    assert sanitized_photo.role == "PROCESSED"
    assert Path(sanitized_photo.path).exists()

    # Verify EXIF is completely absent in output
    with Image.open(sanitized_photo.path) as clean_img:
        exif = clean_img.getexif()
        assert len(exif) == 0

    # 3. Deterministic process
    published_photo = deterministic_process(
        session=session, photo=sanitized_photo, max_dimension=400, storage_dir=storage_dir
    )
    assert published_photo.role == "PUBLISH"
    assert published_photo.width <= 400
    assert published_photo.height <= 400


def test_vlm_verification_rejects_defect_retouch(clean_db_session: Session, tmp_path: Path) -> None:
    session = clean_db_session
    item_code = "777"
    create_test_item(session, item_code)
    storage_dir = tmp_path / "media_storage"

    img_path = tmp_path / "scratch.jpg"
    img = Image.new("RGB", (200, 200), color="blue")
    img.save(img_path)

    orig = ingest_photo(session, item_code, img_path, role="ORIGINAL", storage_dir=storage_dir)
    defect_photo = ingest_photo(
        session, item_code, img_path, role="DEFECT", storage_dir=storage_dir
    )

    # If VLM indicates retouch was detected on defect
    with pytest.raises(VLMVerificationFailedError, match="Defect retouch detected"):
        vlm_verify_photos(
            original=orig,
            candidate=defect_photo,
            item_defects=["deep_scratch_on_screen"],
            retouch_detected=True,
        )

    # If AI generated photo tries to masquerade as defect photo
    defect_photo.ai_generated = True
    session.flush()
    with pytest.raises(VLMVerificationFailedError, match="Defect retouch detected"):
        vlm_verify_photos(
            original=orig,
            candidate=defect_photo,
            item_defects=["deep_scratch_on_screen"],
            retouch_detected=False,
        )


def test_assemble_kit_rules(clean_db_session: Session, tmp_path: Path) -> None:
    session = clean_db_session
    item_code = "888"
    create_test_item(session, item_code)
    storage_dir = tmp_path / "media_storage"

    img_path = tmp_path / "sample.jpg"
    img = Image.new("RGB", (100, 100), color="green")
    img.save(img_path)

    ingest_photo(session, item_code, img_path, role="PUBLISH", storage_dir=storage_dir)
    ingest_photo(session, item_code, img_path, role="DEFECT", storage_dir=storage_dir)

    p_ai = ingest_photo(session, item_code, img_path, role="PUBLISH", storage_dir=storage_dir)
    p_ai.ai_generated = True
    session.flush()

    # Marketplace with allows_generative_photos = False
    caps_no_ai = {"allows_generative_photos": False, "max_photos": 5}
    kit_no_ai = assemble_marketplace_kit(
        session=session,
        item_code=item_code,
        marketplace_caps=caps_no_ai,
        item_defects=["scratch"],
    )
    assert len(kit_no_ai) == 2
    assert not any(p.ai_generated for p in kit_no_ai)

    # Defect declared on an item that has NO defect photo
    item_code_no_defect_photo = "889"
    create_test_item(session, item_code_no_defect_photo)
    ingest_photo(
        session, item_code_no_defect_photo, img_path, role="PUBLISH", storage_dir=storage_dir
    )

    with pytest.raises(PhotoKitAssemblyError, match="no unretouched non-AI defect photo found"):
        assemble_marketplace_kit(
            session=session,
            item_code=item_code_no_defect_photo,
            marketplace_caps=caps_no_ai,
            item_defects=["crack_in_chassis"],
        )

    # Generative photo CANNOT be the first or only photo
    item_code_ai = "890"
    create_test_item(session, item_code_ai)
    p_ai_only = ingest_photo(
        session, item_code_ai, img_path, role="PUBLISH", storage_dir=storage_dir
    )
    p_ai_only.ai_generated = True
    session.flush()

    caps_with_ai = {"allows_generative_photos": True, "max_photos": 5}
    with pytest.raises(PhotoKitAssemblyError, match="cannot be the only photo"):
        assemble_marketplace_kit(
            session=session,
            item_code=item_code_ai,
            marketplace_caps=caps_with_ai,
            item_defects=[],
        )
