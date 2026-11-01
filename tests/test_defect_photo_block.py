import os
from decimal import Decimal
from pathlib import Path
import pytest
from PIL import Image

from nextlife.media.pipeline import (
    PhotoKitAssemblyError,
    assemble_marketplace_kit,
    ingest_photo,
)
from nextlife.storage.db import get_db_session, get_engine
from nextlife.storage.models import Base, Item, ItemPhoto
from scripts.selfcheck_media_advanced import CheckContext, run_c34, validate_c34


@pytest.fixture
def clean_db_item(tmp_path):
    """Ensure clean item in test database for C-34 testing."""
    engine = get_engine()
    Base.metadata.create_all(engine)

    code = "634"
    with get_db_session() as session:
        session.query(ItemPhoto).filter_by(item_code=code).delete()
        session.query(Item).filter_by(code=code).delete()
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
            target_currency="CZK",
        )
        session.add(item)
        session.commit()

        # Create dummy photo
        sample_img = tmp_path / "sample.jpg"
        img = Image.new("RGB", (800, 600), color=(100, 150, 200))
        img.save(sample_img, format="JPEG")

        ingest_photo(session, code, sample_img, role="PUBLISH", storage_dir=tmp_path / "media")

    return code


def test_run_c34_resolves_nextlife_dsn(clean_db_item, tmp_path):
    """DoD: run_c34 connects through NEXTLIFE_DSN (the single DSN source)
    and detects that defects without an unretouched photo block kit assembly.
    """
    # Reset global engine to force re-resolution of the DSN from NEXTLIFE_DSN
    import nextlife.storage.db as db_mod
    if db_mod._engine is not None:
        db_mod._engine.dispose()
        db_mod._engine = None
        db_mod._SessionFactory = None

    ctx = CheckContext(
        base_url="http://127.0.0.1:8000",
        test_dsn=os.environ["NEXTLIFE_DSN"],
        auth_token="dummy",
        out_dir=tmp_path / "out",
        media_dir=tmp_path / "media",
        temp_dir=tmp_path / "temp",
    )

    art_dir = tmp_path / "c34_art"
    art_dir.mkdir(parents=True, exist_ok=True)
    # Pass the pre-seeded item code explicitly: run_c34 is self-contained in
    # the selfcheck (it creates its own lot over HTTP) but this test has no
    # live server, so it operates on the fixture item directly.
    meta = run_c34(ctx, art_dir, code=clean_db_item)
    ok, msg = validate_c34(art_dir, meta)

    assert meta["blocked"] is True, f"Expected kit assembly to be blocked by defect, got: {meta}"
    assert "no unretouched non-AI defect photo found" in meta["error"]
    assert ok is True, f"Expected validate_c34 to pass, but got msg: {msg}"


def test_assemble_marketplace_kit_with_defect_photo(clean_db_item, tmp_path):
    """DoD: If unretouched defect photo exists, assemble_marketplace_kit succeeds."""
    defect_img = tmp_path / "defect.jpg"
    img = Image.new("RGB", (800, 600), color=(200, 50, 50))
    img.save(defect_img, format="JPEG")

    with get_db_session() as session:
        ingest_photo(session, clean_db_item, defect_img, role="DEFECT", storage_dir=tmp_path / "media")

        kit = assemble_marketplace_kit(
            session=session,
            item_code=clean_db_item,
            marketplace_caps={"max_photos": 10, "allows_generative_photos": False},
            item_defects=["DEFECT_TEST_SCRATCH"],
        )
        assert len(kit) >= 2
        defect_photos = [p for p in kit if p.role == "DEFECT"]
        assert len(defect_photos) == 1
        assert defect_photos[0].ai_generated is False


def test_assemble_marketplace_kit_ai_defect_photo_does_not_satisfy_rule(clean_db_item, tmp_path):
    """DoD: If defect photo is ai_generated, it must NOT satisfy the defect photo requirement."""
    ai_defect_img = tmp_path / "defect_ai.jpg"
    img = Image.new("RGB", (800, 600), color=(255, 0, 0))
    img.save(ai_defect_img, format="JPEG")

    with get_db_session() as session:
        p = ingest_photo(session, clean_db_item, ai_defect_img, role="DEFECT", storage_dir=tmp_path / "media")
        p.ai_generated = True
        session.flush()

        with pytest.raises(PhotoKitAssemblyError, match="no unretouched non-AI defect photo found"):
            assemble_marketplace_kit(
                session=session,
                item_code=clean_db_item,
                marketplace_caps={"max_photos": 10, "allows_generative_photos": True},
                item_defects=["DEFECT_TEST_SCRATCH"],
            )
