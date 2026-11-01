"""Tests for Lot Reservation and DB-level Double-Sale Prevention (T-049, Section II.4).

DoD:
- Transition lot to RESERVED with reserved_for_party_id and reserved_until.
- Auto-reply informs counterparty about reservation until specified time.
- Attempt to create second deal on reserved lot strictly fails with a DB-level error (IntegrityError / UniqueViolation).
- Expiration of reservation returns lot to PUBLISHED and marks previous deal EXPIRED.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from nextlife.core.status import ItemStatus
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.reservation import (
    create_deal_for_lot,
    generate_reservation_auto_reply,
    release_expired_reservation,
    reserve_lot,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent, Deal, Item, Listing, Lot, Party, TruthLog


def test_reserve_lot_success_and_truth_audit():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Vintage Camera Leica",
            category_key="general",
            price=Decimal("12000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        item = session.get(Item, lot.item_id)
        # Advance item and lot to PUBLISHED
        item.status = ItemStatus.PUBLISHED.value
        lot.status = ItemStatus.PUBLISHED.value
        session.flush()

        # Reserve lot for buyer-101
        res = reserve_lot(
            session=session,
            lot_code=lot_code,
            party_id="buyer-101",
            reservation_hours=48,
            agreed_price=Decimal("11500.00"),
        )
        session.commit()

        assert res["lot_status"] == ItemStatus.RESERVED.value
        assert res["party_id"] == "buyer-101"
        assert res["reserved_until"] is not None

        # Verify DB lot state
        lot_db = session.scalar(select(Lot).where(Lot.code == lot_code))
        assert lot_db.status == "RESERVED"
        assert lot_db.reserved_for_party_id == "buyer-101"
        assert lot_db.reserved_until is not None

        # Verify Deal in DB
        deal = session.get(Deal, res["deal_id"])
        assert deal is not None
        assert deal.lot_id == lot_db.id
        assert deal.status == "RESERVED"
        assert deal.party_id == "buyer-101"
        assert deal.agreed_price == Decimal("11500.00")

        # Verify TruthLog
        truth = session.scalar(
            select(TruthLog)
            .where(TruthLog.claim.like(f"%{lot_code}%"))
            .order_by(TruthLog.at.desc())
        )
        assert truth is not None
        assert "buyer-101" in truth.claim

        # Verify AuditEvent
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity_id == lot_code, AuditEvent.action == "LOT_RESERVED")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.data["party_id"] == "buyer-101"

        # Cleanup
        session.query(Deal).filter(Deal.lot_id == lot_db.id).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_second_deal_on_reserved_lot_strictly_fails_with_db_constraint():
    """DoD: Attempt to create second deal on reserved lot fails at DB level, not code check."""
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Rare Vinyl Record",
            category_key="general",
            price=Decimal("1500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        item = session.get(Item, lot.item_id)
        item.status = ItemStatus.PUBLISHED.value
        lot.status = ItemStatus.PUBLISHED.value
        session.flush()

        # First deal / reservation
        reserve_lot(
            session=session,
            lot_code=lot_code,
            party_id="buyer-first",
            reservation_hours=24,
            agreed_price=Decimal("1500.00"),
        )
        session.commit()

        # Verify lot is in RESERVED status
        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        assert lot.status == "RESERVED"

        # Ensure party exists for buyer-second
        if not session.get(Party, "buyer-second"):
            session.add(Party(id="buyer-second", name="Buyer Second"))
            session.flush()

        # Attempt to insert a second deal on the same reserved lot directly at DB level
        with pytest.raises(IntegrityError) as exc_info:
            # We bypass any python service checks by invoking raw model insertion
            second_deal = Deal(
                id=str(uuid.uuid4()),
                lot_id=lot.id,
                party_id="buyer-second",
                agreed_price=Decimal("1600.00"),
                currency="CZK",
                price_base=Decimal("1600.00"),
                delivery_method="standard",
                payment_method="cash",
                status="ACTIVE",
            )
            session.add(second_deal)
            session.commit()

        # Database strictly rejected the insert
        session.rollback()
        err_msg = str(exc_info.value).lower()
        # Verify it came from the unique constraint or trigger in postgres
        assert "uq_deal_active_lot" in err_msg or "unique" in err_msg or "cannot create second deal" in err_msg

        # Cleanup
        session.query(Deal).filter(Deal.lot_id == lot.id).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_reservation_auto_reply_generation():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Smartphone Model X",
            category_key="general",
            price=Decimal("8000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        item = session.get(Item, lot.item_id)
        item.status = ItemStatus.PUBLISHED.value
        lot.status = ItemStatus.PUBLISHED.value
        session.flush()

        reserve_lot(
            session=session,
            lot_code=lot_code,
            party_id="buyer-auto",
            reservation_hours=12,
        )
        session.commit()

        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        reply_cs = generate_reservation_auto_reply(lot, lang="cs")
        assert "rezervováno do" in reply_cs
        assert "jiného zájemce" in reply_cs

        reply_en = generate_reservation_auto_reply(lot, lang="en")
        assert "currently reserved until" in reply_en

        # Cleanup
        session.query(Deal).filter(Deal.lot_id == lot.id).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_release_expired_reservation():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Expiring Lot Test",
            category_key="general",
            price=Decimal("3000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        item = session.get(Item, lot.item_id)
        item.status = ItemStatus.PUBLISHED.value
        lot.status = ItemStatus.PUBLISHED.value
        session.flush()

        reserve_lot(
            session=session,
            lot_code=lot_code,
            party_id="buyer-expire",
            reservation_hours=1,
        )
        session.commit()

        # Set reserved_until into past
        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        lot.reserved_until = datetime.now(UTC) - timedelta(hours=2)
        session.commit()

        # Call release_expired_reservation
        res = release_expired_reservation(session, lot_code)
        session.commit()

        assert res["released"] is True
        assert res["new_status"] == ItemStatus.PUBLISHED.value

        # Verify lot returned to PUBLISHED in DB
        lot_after = session.scalar(select(Lot).where(Lot.code == lot_code))
        item_after = session.get(Item, lot.item_id)
        assert lot_after.status == "PUBLISHED"
        assert item_after.status == "PUBLISHED"
        assert lot_after.reserved_for_party_id is None
        assert lot_after.reserved_until is None

        # Verify previous deal is now EXPIRED
        old_deal = session.scalar(select(Deal).where(Deal.lot_id == lot.id))
        assert old_deal.status == "EXPIRED"

        # Now creating a new deal is allowed since the old one is EXPIRED!
        new_deal = create_deal_for_lot(
            session=session,
            lot_id=lot.id,
            party_id="new-buyer-999",
            agreed_price=Decimal("3000.00"),
            status="ACTIVE",
        )
        session.commit()
        assert new_deal.id is not None
        assert new_deal.status == "ACTIVE"

        # Cleanup
        session.query(Deal).filter(Deal.lot_id == lot.id).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()
