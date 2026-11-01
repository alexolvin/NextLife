import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from nextlife.media.models import MediaMaster, MediaOriginal, MediaVariant
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item


def test_variant_cannot_be_created_without_master():
    """DoD: MediaVariant cannot be created without a valid MediaMaster (enforced by DB constraint and FK)."""
    with get_db_session() as session:
        # 1. Attempt creating a variant with no master_id (or None)
        invalid_variant = MediaVariant(
            master_id=None,
            marketplace_key="bazos_cz",
            role="GALLERY",
            profile_hash="hash_123",
            file_path="/tmp/test_variant.jpg",
            sha256="abc",
            width=800,
            height=600,
            file_bytes=50000,
        )
        session.add(invalid_variant)
        with pytest.raises((DBAPIError, IntegrityError)):
            session.commit()
        session.rollback()

        # 2. Attempt creating a variant pointing to non-existent master_id
        orphan_variant = MediaVariant(
            master_id="non_existent_master_id_0000",
            marketplace_key="bazos_cz",
            role="GALLERY",
            profile_hash="hash_123",
            file_path="/tmp/test_variant.jpg",
            sha256="abc",
            width=800,
            height=600,
            file_bytes=50000,
        )
        session.add(orphan_variant)
        with pytest.raises((DBAPIError, IntegrityError)):
            session.commit()
        session.rollback()


def test_three_tier_media_lifecycle():
    """Verify clean 3-tier hierarchy: Original -> Master -> Variant."""
    with get_db_session() as session:
        try:
            item = Item(
                code="994",
                kind="SELL",
                title="GPU for Media Test",
                category_key="hardware",
                attrs={},
                condition_key="used",
                defects={},
                status="DRAFT",
            )
            session.add(item)
            session.commit()

            # 1. Original
            orig = MediaOriginal(
                item_code="994",
                file_path="/var/media/orig_994.jpg",
                sha256="orig_sha256_111",
                file_bytes=2048000,
                exif_data={"Make": "Sony", "Model": "A7"},
            )
            session.add(orig)
            session.commit()

            # 2. Master
            master = MediaMaster(
                original_id=orig.id,
                file_path="/var/media/master_994.jpg",
                sha256="master_sha256_222",
                width=1920,
                height=1080,
                is_cover=True,
            )
            session.add(master)
            session.commit()

            # 3. Variant
            variant = MediaVariant(
                master_id=master.id,
                marketplace_key="bazos_cz",
                role="COVER",
                profile_hash="bazos_profile_v1",
                file_path="/var/media/variant_bazos_994.jpg",
                sha256="variant_sha256_333",
                width=1280,
                height=720,
                file_bytes=350000,
            )
            session.add(variant)
            session.commit()

            # Query and verify
            loaded_variant = session.query(MediaVariant).filter_by(id=variant.id).first()
            assert loaded_variant is not None
            assert loaded_variant.master.original.item_code == "994"
            assert loaded_variant.master.is_cover is True
        finally:
            session.query(MediaVariant).filter_by(profile_hash="bazos_profile_v1").delete()
            session.query(MediaMaster).filter_by(sha256="master_sha256_222").delete()
            session.query(MediaOriginal).filter_by(sha256="orig_sha256_111").delete()
            session.query(Item).filter_by(code="994").delete()
            session.commit()
