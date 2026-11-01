"""Tests for Monthly Sales Summary (TZ v2.0 Section III.8 / Task T-069).

DoD:
- Sold count, gross revenue, platform fees, delivery costs, packaging costs, net payout.
- Average days to sell per category.
- All numbers derived strictly from Settlement table (never external sources).
- Verified by outgoing filter.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.monthly_summary import (
    generate_and_record_monthly_summary,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Item, Lot, Party, Settlement


def test_monthly_summary_financial_reconciliation_and_categories():
    with get_db_session() as session:
        now = datetime.now(UTC)
        year = now.year
        month = now.month

        # Seed Item 1: GPU
        created1 = create_item_and_lot(
            session=session,
            title="Summary GPU Item",
            category_key="gpu",
            price=Decimal("10000.00"),
        )
        lot1 = session.query(Lot).filter(Lot.code == created1["lot_code"]).first()
        party1 = Party(id=str(uuid.uuid4()), name="Summary Buyer 1")
        session.add(party1)
        deal1 = Deal(
            id=str(uuid.uuid4()),
            lot_id=lot1.id,
            party_id=party1.id,
            agreed_price=Decimal("10000.00"),
            currency="CZK",
            price_base=Decimal("10000.00"),
            delivery_method="ZASILKOVNA",
            payment_method="BANK_TRANSFER",
            status="COMPLETED",
        )
        session.add(deal1)
        session.flush()

        settlement1 = Settlement(
            id=str(uuid.uuid4()),
            deal_id=deal1.id,
            gross_amount=Decimal("10000.00"),
            platform_fee=Decimal("500.00"),
            delivery_cost=Decimal("120.00"),
            packaging_cost=Decimal("80.00"),
            net_payout=Decimal("9300.00"),
            currency="CZK",
            payout_status="SETTLED",
            settled_at=now,
        )
        session.add(settlement1)

        # Seed Item 2: Electronics
        created2 = create_item_and_lot(
            session=session,
            title="Summary Headphones Item",
            category_key="electronics",
            price=Decimal("2000.00"),
        )
        lot2 = session.query(Lot).filter(Lot.code == created2["lot_code"]).first()
        party2 = Party(id=str(uuid.uuid4()), name="Summary Buyer 2")
        session.add(party2)
        deal2 = Deal(
            id=str(uuid.uuid4()),
            lot_id=lot2.id,
            party_id=party2.id,
            agreed_price=Decimal("2000.00"),
            currency="CZK",
            price_base=Decimal("2000.00"),
            delivery_method="PERSONAL_PICKUP",
            payment_method="CASH",
            status="COMPLETED",
        )
        session.add(deal2)
        session.flush()

        settlement2 = Settlement(
            id=str(uuid.uuid4()),
            deal_id=deal2.id,
            gross_amount=Decimal("2000.00"),
            platform_fee=Decimal("0.00"),
            delivery_cost=Decimal("0.00"),
            packaging_cost=Decimal("20.00"),
            net_payout=Decimal("1980.00"),
            currency="CZK",
            payout_status="SETTLED",
            settled_at=now,
        )
        session.add(settlement2)
        session.commit()

        try:
            summary, report_text = generate_and_record_monthly_summary(
                session=session,
                year=year,
                month=month,
                currency="CZK",
                actor="TEST_FINANCE",
            )
            session.commit()

            # 1. Total Metrics verification
            assert summary["total_sold_count"] >= 2
            gross_dec = Decimal(summary["total_gross"])
            fees_dec = Decimal(summary["total_platform_fees"])
            deliv_dec = Decimal(summary["total_delivery_costs"])
            pack_dec = Decimal(summary["total_packaging_costs"])
            net_dec = Decimal(summary["total_net_payout"])

            # Math reconciliation: Net must strictly equal Gross - Fees - Delivery - Packaging
            assert net_dec == gross_dec - fees_dec - deliv_dec - pack_dec

            # 2. Categories breakdown verification
            cat_keys = [c["category_key"] for c in summary["by_category"]]
            assert "gpu" in cat_keys
            assert "electronics" in cat_keys

            gpu_stat = next(c for c in summary["by_category"] if c["category_key"] == "gpu")
            assert gpu_stat["sold_count"] >= 1
            assert Decimal(gpu_stat["gross_amount"]) >= Decimal("10000.00")
            assert Decimal(gpu_stat["net_payout"]) >= Decimal("9300.00")
            assert gpu_stat["avg_days_to_sell"] >= 0.0

            # 3. Report Text verification
            assert "МЕСЯЧНАЯ СВОДКА ПРОДАЖ" in report_text
            assert "ОБЩИЕ ФИНАНСОВЫЕ ИТОГИ (ИЗ SETTLEMENT)" in report_text
            assert "ИТОГО НА РУКИ" in report_text
            assert "Gpu" in report_text or "gpu" in report_text
        finally:
            # Cleanup
            session.query(Settlement).filter(Settlement.deal_id.in_([deal1.id, deal2.id])).delete()
            session.query(Deal).filter(Deal.id.in_([deal1.id, deal2.id])).delete()
            session.query(Party).filter(Party.id.in_([party1.id, party2.id])).delete()
            session.query(Lot).filter(Lot.code.in_([lot1.code, lot2.code])).delete()
            session.query(Item).filter(Item.code.in_([created1["item_code"], created2["item_code"]])).delete()
            session.commit()
