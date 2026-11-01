import pytest
from PIL import Image

from nextlife.media.jobs import enqueue_or_get_variant
from nextlife.media.models import MediaMaster, MediaOriginal, MediaVariant
from nextlife.media.profiles import MediaProfile
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Item, Job


@pytest.fixture
def db_master(tmp_path):
    img_path = tmp_path / "master_jobs.png"
    Image.new("RGB", (1000, 800), color=(10, 20, 30)).save(img_path)

    with get_db_session() as session:
        item = Item(
            code="995",
            kind="SELL",
            title="Item for Jobs Test",
            category_key="hardware",
            attrs={},
            condition_key="used",
            defects={},
            status="DRAFT",
        )
        session.add(item)
        session.commit()

        orig = MediaOriginal(
            item_code="995",
            file_path=str(img_path),
            sha256="sha_orig_995",
            file_bytes=1000,
        )
        session.add(orig)
        session.commit()

        master = MediaMaster(
            original_id=orig.id,
            file_path=str(img_path),
            sha256="sha_master_995",
            width=1000,
            height=800,
        )
        session.add(master)
        session.commit()

        master_id = master.id

    yield master_id

    # Teardown
    with get_db_session() as session:
        session.query(MediaVariant).filter(MediaVariant.master_id == master_id).delete()
        session.query(Job).filter(Job.kind == "GENERATE_MEDIA_VARIANT").delete()
        session.query(MediaMaster).filter_by(id=master_id).delete()
        session.query(MediaOriginal).filter_by(sha256="sha_orig_995").delete()
        session.query(Item).filter_by(code="995").delete()
        session.commit()


def test_idempotency_and_stale_marking_on_profile_change(db_master):
    """DoD: Repeat run does not create duplicates; profile change marks old variant stale and enqueues task."""
    with get_db_session() as session:
        profile_v1 = MediaProfile(marketplace_key="bazos_cz", max_width=1280)
        hash_v1 = "hash_profile_version_1"

        # 1. First run: enqueues new job
        variant, job1, created = enqueue_or_get_variant(
            session,
            master_id=db_master,
            marketplace_key="bazos_cz",
            custom_profile=profile_v1,
            custom_profile_hash=hash_v1,
        )
        assert created is True
        assert job1 is not None

        # 2. Second run: idempotency check (does not create duplicate job)
        variant2, job2, created2 = enqueue_or_get_variant(
            session,
            master_id=db_master,
            marketplace_key="bazos_cz",
            custom_profile=profile_v1,
            custom_profile_hash=hash_v1,
        )
        assert created2 is False
        assert job2.id == job1.id

        # Simulate job completion: insert MediaVariant for profile_v1
        v1_record = MediaVariant(
            master_id=db_master,
            marketplace_key="bazos_cz",
            role="GALLERY",
            profile_hash=hash_v1,
            file_path="/tmp/test.jpg",
            sha256="hash1",
            width=1280,
            height=960,
            file_bytes=10000,
            stale=False,
        )
        session.add(v1_record)
        session.commit()

        # 3. Third run: exactly matches existing variant, returns it
        var_found, job3, created3 = enqueue_or_get_variant(
            session,
            master_id=db_master,
            marketplace_key="bazos_cz",
            custom_profile=profile_v1,
            custom_profile_hash=hash_v1,
        )
        assert created3 is False
        assert var_found.id == v1_record.id
        assert var_found.stale is False

        # 4. Profile changes! e.g. marketplace updated specs (hash_v2)
        profile_v2 = MediaProfile(marketplace_key="bazos_cz", max_width=1920)
        hash_v2 = "hash_profile_version_2"

        var_after_change, job_new, created_new = enqueue_or_get_variant(
            session,
            master_id=db_master,
            marketplace_key="bazos_cz",
            custom_profile=profile_v2,
            custom_profile_hash=hash_v2,
        )
        assert created_new is True
        assert job_new is not None

        # DoD: Old variant record must be marked stale=True!
        session.refresh(v1_record)
        assert v1_record.stale is True
