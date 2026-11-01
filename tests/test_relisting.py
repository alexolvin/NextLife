"""Tests for Listing Bump and Relisting Service (T-051, Section I.3, II.1; TZ M1 Ч-5).

DoD:
- In-place bump updates listing where supported (live site action via connector first).
- Relisting creates a new listing with previous_listing_id link, rather than overwriting.
- Previous listing is preserved with ARCHIVED status and history intact.
- Lineage can be traversed through previous_listing_id.
- Ч-5: without a connector performing the live action, no PUBLISHED record and
  no fabricated external id/url are created.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from nextlife.connectors.base import PublishResult
from nextlife.core.config import load_config
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.relisting import (
    RelistingServiceError,
    bump_or_relist_listing,
    get_listing_lineage,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent, Item, Listing, Lot, TruthLog


def _bumpable_config():
    """Test-only config override: a marketplace that supports in-place edit."""
    cfg = load_config()
    cfg.marketplaces["sbazar_cz"].capabilities["edit"] = True
    return cfg


def test_in_place_bump_updates_existing_listing():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Bumpable Item",
            category_key="general",
            price=Decimal("1500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Listing on a marketplace that supports edit/bump (test-only override).
        lst = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key="sbazar_cz",
            external_id="sb-12345",
            external_url="https://sbazar.cz/item/12345",
            price=Decimal("1500.00"),
            currency="CZK",
            price_base=Decimal("1500.00"),
            status="PUBLISHED",
            published_at=datetime.now(UTC),
            publish_evidence={"http_status": 200},
        )
        session.add(lst)
        session.commit()

        orig_id = lst.id
        connector = MagicMock()

        # In-place bump with price reduction to 1350 CZK; the connector
        # performs the live site action (Ч-5).
        res = bump_or_relist_listing(
            session=session,
            listing_id=orig_id,
            new_price=Decimal("1350.00"),
            connector=connector,
            config_bundle=_bumpable_config(),
        )
        session.commit()

        connector.bump.assert_called_once_with("sb-12345")
        assert res["action"] == "BUMPED"
        assert res["in_place"] is True
        assert res["listing_id"] == orig_id
        assert res["price"] == Decimal("1350.00")

        # Verify DB: exactly 1 listing exists, updated in place
        listings = session.scalars(select(Listing).where(Listing.item_code == item_code)).all()
        assert len(listings) == 1
        assert listings[0].id == orig_id
        assert listings[0].price == Decimal("1350.00")
        assert listings[0].status == "PUBLISHED"

        # Verify AuditEvent
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity_id == orig_id, AuditEvent.action == "LISTING_BUMPED")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.data["in_place"] is True

        # Cleanup
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_bump_without_connector_refused():
    """Ч-5: an in-place bump must not be claimed without a live site action."""
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="No Connector Bump",
            category_key="general",
            price=Decimal("1500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        lst = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key="sbazar_cz",
            external_id="sb-12345",
            external_url="https://sbazar.cz/item/12345",
            price=Decimal("1500.00"),
            currency="CZK",
            price_base=Decimal("1500.00"),
            status="PUBLISHED",
            published_at=datetime.now(UTC),
            publish_evidence={"http_status": 200},
        )
        session.add(lst)
        session.commit()

        with pytest.raises(RelistingServiceError, match="requires a connector"):
            bump_or_relist_listing(
                session=session,
                listing_id=lst.id,
                new_price=Decimal("1350.00"),
                config_bundle=_bumpable_config(),
            )
        session.rollback()

        # Nothing changed: single listing, still PUBLISHED at the old price.
        updated = session.get(Listing, lst.id)
        assert updated.price == Decimal("1500.00")
        assert updated.status == "PUBLISHED"

        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_relist_creates_new_listing_linked_to_previous_without_overwriting():
    """DoD: Relisting creates a NEW listing with link to previous, does not overwrite.

    Ч-5: the new external id/url come from the connector's real publication.
    """
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Relisted Item",
            category_key="general",
            price=Decimal("2000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        orig_id = str(uuid.uuid4())
        initial_listing = Listing(
            id=orig_id,
            item_code=item_code,
            marketplace_key="bazos_cz",
            external_id="bz-original-001",
            external_url="https://bazos.cz/item/001",
            price=Decimal("2000.00"),
            currency="CZK",
            price_base=Decimal("2000.00"),
            status="PUBLISHED",
            published_at=datetime.now(UTC),
            publish_evidence={"http_status": 200},
        )
        session.add(initial_listing)
        session.commit()

        connector = MagicMock()
        connector.publish.return_value = PublishResult(
            external_id="bz-relist-002",
            external_url="https://bazos.cz/item/002",
            evidence={"http_status": 200},
        )

        # Perform relist (force_relist=True), live re-publication via connector.
        res = bump_or_relist_listing(
            session=session,
            listing_id=orig_id,
            new_price=Decimal("1800.00"),
            force_relist=True,
            connector=connector,
        )
        session.commit()

        connector.publish.assert_called_once()
        # DoD Verification
        assert res["action"] == "RELISTED"
        assert res["in_place"] is False
        new_id = res["listing_id"]
        assert new_id != orig_id
        assert res["previous_listing_id"] == orig_id

        # Verify in DB: BOTH listings exist
        listings = session.scalars(
            select(Listing).where(Listing.item_code == item_code).order_by(Listing.created_at.asc())
        ).all()
        assert len(listings) == 2

        old_db = session.get(Listing, orig_id)
        new_db = session.get(Listing, new_id)

        # Old listing is preserved with ARCHIVED status
        assert old_db is not None
        assert old_db.status == "ARCHIVED"
        assert old_db.archived_at is not None
        assert old_db.price == Decimal("2000.00")

        # New listing is PUBLISHED with link to old listing; external data is
        # the connector's, not fabricated by the service.
        assert new_db is not None
        assert new_db.status == "PUBLISHED"
        assert new_db.price == Decimal("1800.00")
        assert new_db.previous_listing_id == orig_id
        assert new_db.external_id == "bz-relist-002"
        assert new_db.external_url == "https://bazos.cz/item/002"
        assert new_db.publish_evidence["relisted_from"] == orig_id

        # Verify lineage traversal
        lineage = get_listing_lineage(session, new_id)
        assert len(lineage) == 2
        assert lineage[0]["id"] == new_id
        assert lineage[0]["previous_listing_id"] == orig_id
        assert lineage[1]["id"] == orig_id
        assert lineage[1]["previous_listing_id"] is None

        # Verify TruthLog
        truth = session.scalar(
            select(TruthLog)
            .where(TruthLog.claim.like(f"%{new_id}%"))
            .order_by(TruthLog.at.desc())
        )
        assert truth is not None
        assert orig_id in truth.evidence["previous_listing_id"]

        # Cleanup
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_relist_without_connector_refused_no_fabrication():
    """Ч-5: without a connector the service must refuse, not fabricate a
    'relisted' PUBLISHED listing with an invented external id/url."""
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="No Connector Relist",
            category_key="general",
            price=Decimal("2000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        orig_id = str(uuid.uuid4())
        initial_listing = Listing(
            id=orig_id,
            item_code=item_code,
            marketplace_key="sbazar_cz",
            external_id="sb-original-001",
            external_url="https://sbazar.cz/item/001",
            price=Decimal("2000.00"),
            currency="CZK",
            price_base=Decimal("2000.00"),
            status="PUBLISHED",
            published_at=datetime.now(UTC),
            publish_evidence={"http_status": 200},
        )
        session.add(initial_listing)
        session.commit()

        with pytest.raises(RelistingServiceError, match="requires a connector"):
            bump_or_relist_listing(
                session=session,
                listing_id=orig_id,
                new_price=Decimal("1800.00"),
                force_relist=True,
            )
        session.rollback()

        # Nothing fabricated, nothing archived.
        updated = session.get(Listing, orig_id)
        assert updated.status == "PUBLISHED"
        assert updated.archived_at is None
        assert session.query(Listing).filter(Listing.item_code == item_code).count() == 1

        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()
