"""Tests for Lot deletion: active deal rejection, marketplace unpublishing, and audit (T-044).

DoD:
- Deletion rejected with detailed reason if an active deal exists.
- Active marketplace listings are automatically unpublished/archived prior to deletion.
- Soft deletion retains record with status DELETED.
- Deleted lot remains permanently recorded in AuditEvent.
- Module length <= 400 lines.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.lot_delete import LotDeletionBlockedError, can_delete_lot, delete_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import AuditEvent, Deal, Listing, Lot


class _StubUnpublishConnector:
    """ТЗ «Склад» §2.4: коннектор-заглушка с успешным unpublish."""

    def unpublish(self, external_id: str) -> None:
        return None


def test_delete_lot_blocked_by_active_deal():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Item With Active Deal",
            category_key="electronics",
            price=Decimal("3000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Create listing
        listing_id = str(uuid.uuid4())
        listing = Listing(
            id=listing_id,
            item_code=item_code,
            marketplace_key="bazos_cz",
            price=Decimal("3000.00"),
            currency="CZK",
            price_base=Decimal("3000.00"),
            status="PUBLISHED",
        )
        session.add(listing)
        session.flush()

        # Create active deal
        deal_id = str(uuid.uuid4())
        deal = Deal(
            id=deal_id,
            listing_id=listing_id,
            agreed_price=Decimal("3000.00"),
            currency="CZK",
            price_base=Decimal("3000.00"),
            delivery_method="HANDOVER",
            payment_method="CASH",
            status="ACTIVE",
            closed_at=None,
            closure_evidence={},
        )
        session.add(deal)
        session.commit()

        # Check can_delete_lot
        allowed, reason = can_delete_lot(session, lot_code)
        assert allowed is False
        assert f"Невозможно удалить лот #{lot_code}" in reason
        assert deal_id in reason

        # Attempt to delete -> must raise LotDeletionBlockedError
        with pytest.raises(LotDeletionBlockedError) as exc_info:
            delete_lot(session, lot_code=lot_code)
        assert deal_id in str(exc_info.value)
        assert "активная сделка" in str(exc_info.value)

        # Ensure lot was NOT deleted or modified
        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        assert lot is not None
        assert lot.status != "DELETED"

        # Clean up deal and listing
        session.delete(deal)
        session.delete(listing)
        session.commit()


def test_delete_lot_unpublishes_listings_and_soft_deletes_with_audit(monkeypatch):
    # ТЗ «Склад» §2.4: снятие через коннекторы реестра — заглушка с успехом.
    from nextlife.connectors import registry

    monkeypatch.setattr(registry, "get_connector", lambda key, cfg: _StubUnpublishConnector())
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Item To Delete",
            category_key="general",
            price=Decimal("1500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Add two published listings
        l1_id = str(uuid.uuid4())
        l1 = Listing(
            id=l1_id,
            item_code=item_code,
            marketplace_key="bazos_cz",
            price=Decimal("1500.00"),
            currency="CZK",
            price_base=Decimal("1500.00"),
            status="PUBLISHED",
        )
        l2_id = str(uuid.uuid4())
        l2 = Listing(
            id=l2_id,
            item_code=item_code,
            marketplace_key="sbazar_cz",
            price=Decimal("1500.00"),
            currency="CZK",
            price_base=Decimal("1500.00"),
            status="PARTIALLY_PUBLISHED",
        )
        session.add_all([l1, l2])
        session.commit()

        # Perform deletion
        res = delete_lot(session, lot_code=lot_code, actor="OWNER")
        session.commit()

        assert res["success"] is True
        assert res["status"] == "DELETED"
        assert set(res["unpublished_marketplaces"]) == {"bazos_cz", "sbazar_cz"}

        # Verify listings are now ARCHIVED
        l1_updated = session.get(Listing, l1_id)
        l2_updated = session.get(Listing, l2_id)
        assert l1_updated.status == "ARCHIVED"
        assert l1_updated.archived_at is not None
        assert l2_updated.status == "ARCHIVED"
        assert l2_updated.archived_at is not None

        # Verify soft delete: Lot record still exists with status DELETED
        lot = session.scalar(select(Lot).where(Lot.code == lot_code))
        assert lot is not None
        assert lot.status == "DELETED"

        # Verify AuditEvent recorded and queryable
        audit = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.entity == "lot", AuditEvent.entity_id == lot_code, AuditEvent.action == "LOT_DELETED")
            .order_by(AuditEvent.at.desc())
        )
        assert audit is not None
        assert audit.actor == "OWNER"
        assert audit.data["new_status"] == "DELETED"
        assert audit.data["media_preserved"] is True
        assert set(audit.data["unpublished_marketplaces"]) == {"bazos_cz", "sbazar_cz"}

        # Clean up
        session.delete(l1_updated)
        session.delete(l2_updated)
        session.commit()

