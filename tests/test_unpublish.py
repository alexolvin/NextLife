"""Tests for Multi-Marketplace Unpublish Service upon Sale (T-050, Section I.3).

DoD:
- When sale confirmed on one marketplace, tasks are queued for all other marketplaces with deadline.
- While at least one unpublish is unconfirmed/failed, lot remains in RESERVED, NOT in SOLD.
- Owner clearly sees which marketplace unpublishing failed on.
- When all unpublishes are confirmed, lot transitions to SOLD.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from nextlife.core.status import ItemStatus
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.unpublish import (
    execute_unpublish_tasks,
    get_unpublish_status,
    initiate_unpublish_on_sale,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent, Item, Job, Listing, Lot, TruthLog


def _setup_lot_with_three_listings(session):
    created = create_item_and_lot(
        session=session,
        title="Multi-Listing Item",
        category_key="general",
        price=Decimal("4500.00"),
    )
    lot_code = created["lot_code"]
    item_code = created["item_code"]

    item = session.get(Item, item_code)
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    item.status = ItemStatus.PUBLISHED.value
    lot.status = ItemStatus.PUBLISHED.value

    # Create listings for bazos_cz, aukro_cz, sbazar_cz
    for mp_key in ["bazos_cz", "aukro_cz", "sbazar_cz"]:
        lst = Listing(
            id=str(uuid.uuid4()),
            item_code=item_code,
            marketplace_key=mp_key,
            external_id=f"ext-{mp_key}-{lot_code}",
            external_url=f"https://{mp_key}.cz/item/{lot_code}",
            price=Decimal("4500.00"),
            currency="CZK",
            price_base=Decimal("4500.00"),
            status="PUBLISHED",
            published_at=datetime.now(UTC),
            publish_evidence={"http_status": 200},
        )
        session.add(lst)
    session.flush()
    return lot_code, item_code


def test_initiate_unpublish_creates_jobs_and_sets_reserved():
    with get_db_session() as session:
        lot_code, item_code = _setup_lot_with_three_listings(session)

        # Sale confirmed on bazos_cz
        res = initiate_unpublish_on_sale(
            session=session,
            lot_code=lot_code,
            sold_marketplace_key="bazos_cz",
            deadline_minutes=15,
        )
        session.commit()

        # DoD: Lot status is RESERVED, NOT SOLD
        assert res["lot_status"] == ItemStatus.RESERVED.value
        assert len(res["tasks"]) == 2
        task_mps = {t["marketplace_key"] for t in res["tasks"]}
        assert task_mps == {"aukro_cz", "sbazar_cz"}

        # Check DB state
        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        assert lot.status == "RESERVED"

        # Check AuditEvent
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity_id == lot_code, AuditEvent.action == "UNPUBLISH_INITIATED")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.data["sold_marketplace"] == "bazos_cz"

        # Cleanup
        session.query(Job).filter(Job.payload["lot_code"].as_string() == lot_code).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_unconfirmed_unpublish_keeps_lot_in_reserved_not_sold():
    """DoD: While at least one unpublish is unconfirmed, lot stays in RESERVED; owner sees failure."""
    with get_db_session() as session:
        lot_code, item_code = _setup_lot_with_three_listings(session)

        initiate_unpublish_on_sale(
            session=session,
            lot_code=lot_code,
            sold_marketplace_key="bazos_cz",
            deadline_minutes=15,
        )
        session.commit()

        # aukro succeeds, but sbazar fails
        outcomes = {
            "aukro_cz": True,
            "sbazar_cz": False,
        }
        res = execute_unpublish_tasks(
            session=session,
            lot_code=lot_code,
            mock_outcomes=outcomes,
        )
        session.commit()

        # DoD: Lot must NOT transition to SOLD, must stay in RESERVED
        assert res["lot_status"] == ItemStatus.RESERVED.value
        assert res["all_confirmed"] is False
        assert "aukro_cz" in res["successes"]
        assert "sbazar_cz" in res["failed_marketplaces"]

        # Owner status view shows failure clearly
        status_info = get_unpublish_status(session, lot_code)
        assert status_info["can_transition_to_sold"] is False
        assert "sbazar_cz" in status_info["failed_marketplaces"]
        assert "aukro_cz" in status_info["completed_marketplaces"]

        # Cleanup
        session.query(Job).filter(Job.payload["lot_code"].as_string() == lot_code).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_deadline_exceeded_records_audit_warning():
    with get_db_session() as session:
        lot_code, item_code = _setup_lot_with_three_listings(session)

        initiate_unpublish_on_sale(
            session=session,
            lot_code=lot_code,
            sold_marketplace_key="bazos_cz",
            deadline_minutes=1,
        )
        session.commit()

        # Fast-forward jobs deadline into past
        jobs = session.scalars(
            select(Job).where(Job.payload["lot_code"].as_string() == lot_code)
        ).all()
        past_deadline = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        for j in jobs:
            payload = dict(j.payload)
            payload["deadline"] = past_deadline
            j.payload = payload
        session.commit()

        # Execute with failure on sbazar_cz
        res = execute_unpublish_tasks(
            session=session,
            lot_code=lot_code,
            mock_outcomes={"aukro_cz": True, "sbazar_cz": False},
        )
        session.commit()

        assert res["deadline_exceeded"] is True
        assert res["lot_status"] == ItemStatus.RESERVED.value

        # Verify audit event for deadline exceeded
        audit = session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.entity_id == lot_code,
                AuditEvent.action == "UNPUBLISH_DEADLINE_EXCEEDED",
            )
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert "sbazar_cz" in audit.data["failed_marketplaces"]

        # Cleanup
        session.query(Job).filter(Job.payload["lot_code"].as_string() == lot_code).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_all_unpublishes_confirmed_transitions_lot_to_sold():
    """DoD: When all unpublishes are confirmed, lot successfully transitions to SOLD."""
    with get_db_session() as session:
        lot_code, item_code = _setup_lot_with_three_listings(session)

        initiate_unpublish_on_sale(
            session=session,
            lot_code=lot_code,
            sold_marketplace_key="bazos_cz",
            deadline_minutes=15,
        )
        session.commit()

        # Both unpublish tasks succeed
        res = execute_unpublish_tasks(
            session=session,
            lot_code=lot_code,
            mock_outcomes={"aukro_cz": True, "sbazar_cz": True},
        )
        session.commit()

        # DoD: Lot transitions to SOLD
        assert res["lot_status"] == ItemStatus.SOLD.value
        assert res["item_status"] == ItemStatus.SOLD.value
        assert res["all_confirmed"] is True
        assert set(res["successes"]) == {"aukro_cz", "sbazar_cz"}

        # Verify DB lot state is SOLD
        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        assert lot.status == "SOLD"

        # Verify TruthLog entry
        truth = session.scalar(
            select(TruthLog)
            .where(TruthLog.claim.like(f"%{lot_code}%"))
            .order_by(TruthLog.at.desc())
        )
        assert truth is not None
        assert "SOLD" in truth.claim

        # Verify AuditEvent
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity_id == lot_code, AuditEvent.action == "LOT_SOLD")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.data["status"] == "SOLD"

        # Cleanup
        session.query(Job).filter(Job.payload["lot_code"].as_string() == lot_code).delete()
        session.query(Listing).filter(Listing.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()
