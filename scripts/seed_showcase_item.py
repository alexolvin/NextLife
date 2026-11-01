#!/usr/bin/env python3
"""Seed realistic showcase data for Web-UI verification and Playwright testing."""

from datetime import UTC, datetime
from decimal import Decimal

from nextlife.core.status import ItemStatus
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    Deal,
    Item,
    ItemPhoto,
    Listing,
    Marketplace,
    Message,
    Thread,
)


def seed_showcase() -> str:
    with get_db_session() as session:
        # Check or create marketplace
        mp = session.query(Marketplace).filter_by(key="bazos").first()
        if not mp:
            mp = Marketplace(
                key="bazos",
                title="Bazos.cz",
                country="CZ",
                currency="CZK",
                access_mode="DIRECT",
                capabilities={"max_photos": 10},
                enabled=True,
                config_hash="default_bazos_hash",
            )
            session.add(mp)
            session.commit()

        # Delete existing 580 if any
        from nextlife.storage.models import Approval
        session.query(Message).filter(Message.thread_id == "thread-580-demo").delete()
        session.query(Thread).filter_by(id="thread-580-demo").delete()
        session.query(Deal).filter_by(id="deal-580-demo").delete()
        session.query(Listing).filter_by(item_code="580").delete()
        session.query(Approval).filter_by(item_code="580").delete()
        session.query(ItemPhoto).filter_by(item_code="580").delete()
        session.query(Item).filter_by(code="580").delete()
        session.commit()

        # Create Item 580
        item = Item(
            code="580",
            kind="SELL",
            title="Sony Alpha A6000 + 16-50mm Kit",
            category_key="electronics",
            attrs={
                "brand": "Sony",
                "model": "A6000",
                "condition": "Very good",
                "shutter_count": 4200,
                "pickup_allowed": True,
            },
            condition_key="used",
            defects={"minor_scratch": "Легкая потертость на нижней грани корпуса"},
            status=ItemStatus.AWAITING_APPROVAL.value,
            target_price=Decimal("380.00"),
            target_currency="EUR",
            notes="Grok AI: Рекомендованный коридор цен 360–400 EUR. Линзы чистые, байонет в идеале.",
        )
        session.add(item)
        session.flush()

        # Add Photos
        p1 = ItemPhoto(
            id="photo-580-orig",
            item_code="580",
            role="ORIGINAL",
            path="/static/demo/sony_front.jpg",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            width=1920,
            height=1080,
            transform_log={"orientation": 1, "sanitized": True},
            ai_generated=False,
            vlm_verdict={"authentic": True, "defect_preserved": True},
        )
        p2 = ItemPhoto(
            id="photo-580-defect",
            item_code="580",
            role="DEFECT",
            path="/static/demo/sony_scratch.jpg",
            sha256="ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
            width=1920,
            height=1080,
            transform_log={"orientation": 1, "sanitized": True},
            ai_generated=False,
            vlm_verdict={"authentic": True, "defect_preserved": True},
        )
        p3 = ItemPhoto(
            id="photo-580-publish",
            item_code="580",
            role="PUBLISH",
            path="/static/demo/sony_catalog.jpg",
            sha256="da978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
            width=1920,
            height=1080,
            transform_log={"orientation": 1, "sanitized": True, "white_balance": True},
            ai_generated=True,
            vlm_verdict={"authentic": True, "note": "Defect preserved, background cleaned"},
        )
        session.add(p1)
        session.add(p2)
        session.add(p3)

        # Ensure sample listing for Inbox and Archive
        listing = session.query(Listing).filter_by(item_code="580").first()
        if not listing:
            listing = Listing(
                id="listing-580-bazos",
                item_code="580",
                marketplace_key="bazos",
                external_id="bazos-192837",
                external_url="https://bazos.cz/inzerat/192837",
                price=Decimal("9500.00"),
                currency="CZK",
                price_base=Decimal("380.00"),
                status="ACTIVE",
                publish_evidence={"posted": True},
            )
            session.add(listing)
            session.flush()

        # Sample thread and message for Inbox
        th = session.query(Thread).filter_by(id="thread-580-demo").first()
        if not th:
            th = Thread(
                id="thread-580-demo",
                listing_id=listing.id,
                channel="MARKETPLACE",
                counterparty="Jan Novak",
                lang="cs",
                status="OPEN",
            )
            session.add(th)
            session.flush()

            msg = Message(
                id="msg-580-demo",
                thread_id=th.id,
                direction="IN",
                transport="MARKETPLACE",
                text_original="Dobrý den, je fotoaparát stále k dispozici? Mohu se zítra stavit na Praze 2?",
                lang_original="cs",
                text_ru="Добрый день, фотоаппарат еще доступен? Могу завтра заехать в Прагу 2?",
                answered_by="AGENT",
                template_key="pickup_available",
                escalated=False,
                source_evidence={"raw": "test"},
            )
            session.add(msg)

        # Sample archived deal
        deal = session.query(Deal).filter_by(id="deal-580-demo").first()
        if not deal:
            deal = Deal(
                id="deal-580-demo",
                listing_id=listing.id,
                agreed_price=Decimal("380.00"),
                currency="EUR",
                price_base=Decimal("380.00"),
                delivery_method="PERSONAL_PICKUP",
                payment_method="CASH",
                status="COMPLETED",
                closed_at=datetime.now(UTC),
                closure_evidence={"confirmed": True},
            )
            session.add(deal)

        session.commit()
        print("Successfully seeded showcase item #580 with photos, messages, and deals.")
        return "580"


if __name__ == "__main__":
    seed_showcase()
