"""Tests for Deal, Party, Handover, Settlement models and PII retention cleanup (T-057, Section II.1 & III).

DoD:
- Models for deal, party, handover, settlement work with relational integrity.
- Retention period for personal data configured in nextlife.yaml.
- Clean-up task applies retention rule to purge and anonymize expired counterparty data.
- Module length <= 400 lines.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.core.config import load_config
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.privacy import purge_expired_pii
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    AuditEvent,
    Deal,
    Handover,
    Listing,
    Lot,
    Party,
    Settlement,
)


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def _create_lot_and_listing(session, price=Decimal("5000.00")):
    created = create_item_and_lot(
        session=session,
        title="MacBook Air M2 16GB",
        category_key="electronics",
        price=price,
    )
    lot_code = created["lot_code"]
    item_code = created["item_code"]

    listing = Listing(
        id=str(uuid.uuid4()),
        item_code=item_code,
        marketplace_key="bazos_cz",
        external_id=f"bazos-{lot_code}",
        external_url=f"https://bazos.cz/item/{lot_code}",
        price=price,
        currency="CZK",
        price_base=price,
        status="PUBLISHED",
        published_at=datetime.now(UTC),
    )
    session.add(listing)
    session.flush()

    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    return lot, listing


def test_deal_party_handover_settlement_models_crud(db):
    lot, listing = _create_lot_and_listing(db)

    # 1. Create Party with minimal PII
    party = Party(
        id=str(uuid.uuid4()),
        name="Tomáš Dvořák",
        phone="+420777111222",
        email="tomas.dvorak@example.cz",
        shipping_address="Vinohradská 12, Praha 2",
        bank_account="CZ6508000000001920001453",
    )
    db.add(party)
    db.flush()

    # 2. Create Deal
    deal = Deal(
        id=str(uuid.uuid4()),
        lot_id=lot.id,
        party_id=party.id,
        listing_id=listing.id,
        agreed_price=Decimal("4800.00"),
        currency="CZK",
        price_base=Decimal("4800.00"),
        delivery_method="ZASILKOVNA",
        payment_method="BANK_TRANSFER",
        status="RESERVED",
    )
    db.add(deal)
    db.flush()

    # 3. Create Handover
    handover = Handover(
        id=str(uuid.uuid4()),
        deal_id=deal.id,
        method="ZASILKOVNA",
        place="Z-BOX Vinohradská 12",
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
        tracking_number="Z1234567890",
        tracking_url="https://tracking.packeta.com/cs/Z1234567890",
        status="PENDING",
        evidence={"dispatch_point_id": "1425"},
    )
    db.add(handover)
    db.flush()

    # 4. Create Settlement with breakdown
    gross = Decimal("4800.00")
    fee = Decimal("0.00")
    shipping = Decimal("89.00")
    packaging = Decimal("35.00")
    net = gross - fee - shipping - packaging

    settlement = Settlement(
        id=str(uuid.uuid4()),
        deal_id=deal.id,
        gross_amount=gross,
        platform_fee=fee,
        delivery_cost=shipping,
        packaging_cost=packaging,
        net_payout=net,
        currency="CZK",
        payout_status="PENDING",
    )
    db.add(settlement)
    db.flush()

    # Verify relationships
    assert deal.party.email == "tomas.dvorak@example.cz"
    assert deal.handover.tracking_number == "Z1234567890"
    assert deal.settlement.net_payout == Decimal("4676.00")
    assert len(party.deals) == 1
    assert party.deals[0].id == deal.id


def test_privacy_retention_purges_expired_pii(db):
    lot, listing = _create_lot_and_listing(db)

    party = Party(
        id=str(uuid.uuid4()),
        name="Jana Veselá",
        phone="+420608999888",
        email="jana.vesela@example.cz",
        shipping_address="Nádražní 5, Brno",
        bank_account="CZ1234567890123456789012",
    )
    db.add(party)
    db.flush()

    now = datetime.now(UTC)
    # Deal completed 35 days ago (configured default retention is 30 days)
    deal = Deal(
        id=str(uuid.uuid4()),
        lot_id=lot.id,
        party_id=party.id,
        listing_id=listing.id,
        agreed_price=Decimal("3500.00"),
        currency="CZK",
        price_base=Decimal("3500.00"),
        delivery_method="PERSONAL",
        payment_method="CASH",
        status="COMPLETED",
        closed_at=now - timedelta(days=35),
    )
    db.add(deal)

    handover = Handover(
        id=str(uuid.uuid4()),
        deal_id=deal.id,
        method="PERSONAL",
        place="Nádražní 5, Brno (domácí adresa)",
        status="CONFIRMED",
    )
    db.add(handover)
    db.flush()

    # Execute purge with default retention (from config: 30 days)
    config = load_config()
    assert config.app.pii_retention_days == 30

    result = purge_expired_pii(session=db, now=now)
    assert result["purged_deals_count"] >= 1
    assert result["retention_days"] == 30

    # Verify PII purged
    db.refresh(deal)
    db.refresh(party)
    db.refresh(handover)

    assert deal.pii_cleared_at is not None
    assert party.anonymized is True
    assert party.pii_cleared_at is not None
    assert party.phone is None
    assert party.email is None
    assert party.shipping_address is None
    assert party.bank_account is None
    assert "Anonymized Party" in party.name
    assert handover.place == "[PII_PURGED]"

    # Verify audit event recorded
    audit = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "PII_PURGED")
    ).all()
    assert len(audit) >= 1
    assert audit[-1].data["retention_days"] == 30


def test_active_deal_prevents_party_pii_purge(db):
    lot1, listing1 = _create_lot_and_listing(db)
    lot2, listing2 = _create_lot_and_listing(db)

    party = Party(
        id=str(uuid.uuid4()),
        name="Karel Hynek",
        phone="+420721000111",
        email="karel.hynek@example.cz",
        shipping_address="Lipová 4, Olomouc",
    )
    db.add(party)
    db.flush()

    now = datetime.now(UTC)
    # Deal 1: closed 40 days ago
    deal1 = Deal(
        id=str(uuid.uuid4()),
        lot_id=lot1.id,
        party_id=party.id,
        listing_id=listing1.id,
        agreed_price=Decimal("1000.00"),
        currency="CZK",
        price_base=Decimal("1000.00"),
        delivery_method="PERSONAL",
        payment_method="CASH",
        status="COMPLETED",
        closed_at=now - timedelta(days=40),
    )
    # Deal 2: still ACTIVE (RESERVED)
    deal2 = Deal(
        id=str(uuid.uuid4()),
        lot_id=lot2.id,
        party_id=party.id,
        listing_id=listing2.id,
        agreed_price=Decimal("2000.00"),
        currency="CZK",
        price_base=Decimal("2000.00"),
        delivery_method="PERSONAL",
        payment_method="CASH",
        status="RESERVED",
    )
    db.add_all([deal1, deal2])
    db.flush()

    # Run purge with 30 days retention
    purge_expired_pii(session=db, retention_days=30, now=now)

    db.refresh(deal1)
    db.refresh(deal2)
    db.refresh(party)

    # Deal 1 was cleared
    assert deal1.pii_cleared_at is not None
    # Deal 2 is still active
    assert deal2.pii_cleared_at is None
    # Party PII must NOT be anonymized because deal 2 is active!
    assert party.anonymized is False
    assert party.email == "karel.hynek@example.cz"
    assert party.phone == "+420721000111"
