#!/usr/bin/env python3
"""Seed authentic showcase data matching real Czech advertisements."""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from nextlife.core.status import ItemStatus
from nextlife.obs.audit import record_audit_event
from nextlife.storage.db import get_db_session
from nextlife.storage.models import (
    Approval,
    Deal,
    Item,
    ItemPhoto,
    Listing,
    Lot,
    Marketplace,
    Message,
    ResearchResult,
    ResearchRun,
    Thread,
)


def compute_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def seed():
    with get_db_session() as session:
        # 1. Marketplaces
        for m_key, title, curr in [("bazos_cz", "Bazoš.cz", "CZK"), ("sbazar_cz", "Sbazar.cz", "CZK"), ("aukro_cz", "Aukro.cz", "CZK")]:
            mp = session.query(Marketplace).filter_by(key=m_key).first()
            if not mp:
                mp = Marketplace(
                    key=m_key, title=title, country="CZ", currency=curr,
                    access_mode="DIRECT", capabilities={"max_photos": 10},
                    enabled=True, config_hash=f"hash_{m_key}",
                )
                session.add(mp)
        session.commit()

        # Clean existing items 777, 802, 348 and 580
        for code in ["777", "802", "348", "580"]:
            session.query(Message).filter(Message.thread_id.like(f"thread-{code}%")).delete(synchronize_session=False)
            session.query(Thread).filter(Thread.id.like(f"thread-{code}%")).delete(synchronize_session=False)
            session.query(Deal).filter(Deal.id.like(f"deal-{code}%")).delete(synchronize_session=False)
            session.query(ResearchResult).filter(ResearchResult.id.like(f"rr-{code}%")).delete(synchronize_session=False)
            session.query(ResearchRun).filter_by(item_code=code).delete(synchronize_session=False)
            session.query(Listing).filter_by(item_code=code).delete(synchronize_session=False)
            session.query(Approval).filter_by(item_code=code).delete(synchronize_session=False)
            session.query(Lot).filter_by(item_id=code).delete(synchronize_session=False)
            session.query(ItemPhoto).filter_by(item_code=code).delete(synchronize_session=False)
            session.query(Item).filter_by(code=code).delete(synchronize_session=False)
        session.commit()

        # ==========================================
        # ITEM 1: #777 (Lenovo ThinkPad P14s Gen 5) - Match to live research E3-R
        # ==========================================
        it777 = Item(
            code="777",
            kind="SELL",
            title="Lenovo ThinkPad P14s Gen 5 (Ryzen 7 8840HS, 32GB RAM)",
            category_key="electronics",
            attrs={
                "brand": "Lenovo",
                "model": "ThinkPad P14s Gen 5",
                "cpu": "AMD Ryzen 7 PRO 8840HS (8C/16T, 5.1 GHz)",
                "ram": "32 GB DDR5 5600MHz",
                "storage": "1 TB NVMe SSD",
                "display": "14.0\" WUXGA (1920x1200) IPS 400 nits Low Power",
                "battery": "52 Wh (14 cyklů, 100% stav)",
                "keyboard": "Podsvícená CZ/SK layout",
                "os": "Windows 11 Pro (licence)",
                "warranty": "Záruka Premier Support do 03/2027",
                "pickup_allowed": True,
                "listing_text_cs": (
                    "Prodám prémiovou mobilní pracovní stanici Lenovo ThinkPad P14s Gen 5 v perfektním stavu.\n"
                    "Procesor AMD Ryzen 7 PRO 8840HS (8 jader/16 vláken, boost až 5.1 GHz), 32 GB RAM DDR5, "
                    "1 TB rychlé NVMe SSD, 14\" IPS displej 1920x1200 400 nitů Low Power.\n"
                    "Oficiální mezinárodní záruka Lenovo Premier Support platná do 03/2027.\n"
                    "Drobný kosmetický detail: mikroskopická oděrka 2 mm na spodním krytu u mřížky (viz detailní foto).\n"
                    "Osobní předání v Praze s možností vyzkoušení, případně zaslání přes Zásilkovnu/PPL."
                ),
            },
            condition_key="used",
            defects={"bottom_scuff": "Микропотертость 2 мм на нижней крышке корпуса возле вентиляционной решетки"},
            status=ItemStatus.AWAITING_APPROVAL.value,
            target_price=Decimal("1192.00"),
            target_currency="EUR",
            notes="Grok AI: Медиана рынка Bazoš.cz — 29 800 CZK (~1 192 EUR). Ликвидность высокая, состояние близкое к новому.",
        )
        session.add(it777)
        session.flush()

        lot777 = Lot(
            id="lot-777",
            code="777",
            item_id="777",
            status=ItemStatus.AWAITING_APPROVAL.value,
            price_gross=Decimal("29800.00"),
            price_floor=Decimal("27000.00"),
            currency="CZK",
            shipping_policy="BUYER_PAYS",
            notes="Lenovo ThinkPad P14s Gen 5",
        )
        session.add(lot777)

        # Photos for 777
        p777_1_path = Path("data/media/777/originals/thinkpad_front.jpg").resolve()
        p777_2_path = Path("data/media/777/originals/thinkpad_defect.jpg").resolve()
        p777_3_path = Path("data/media/777/processed/thinkpad_publish.jpg").resolve()

        p777_1 = ItemPhoto(
            id="p-777-front", item_code="777", role="ORIGINAL",
            path=str(p777_1_path), sha256=compute_sha(p777_1_path), width=640, height=640,
            transform_log={"orientation": 1, "sanitized": True}, ai_generated=False,
            vlm_verdict={"authentic": True, "verdict": "REAL_PHOTO"},
        )
        p777_2 = ItemPhoto(
            id="p-777-defect", item_code="777", role="DEFECT",
            path=str(p777_2_path), sha256=compute_sha(p777_2_path), width=640, height=640,
            transform_log={"orientation": 1, "macro": True}, ai_generated=False,
            vlm_verdict={"authentic": True, "defect_confirmed": True, "note": "Дефект зафиксирован точно"},
        )
        p777_3 = ItemPhoto(
            id="p-777-publish", item_code="777", role="PUBLISH",
            path=str(p777_3_path), sha256=compute_sha(p777_3_path), width=640, height=640,
            transform_log={"white_balance": True, "background": "clean_white"}, ai_generated=True,
            vlm_verdict={"authentic": True, "defect_preserved": True, "note": "Без ретуши дефектов"},
        )
        session.add_all([p777_1, p777_2, p777_3])

        # Research run for 777
        rr777 = ResearchRun(
            id="run-777-bazos", item_code="777",
            status="FINISHED",
            marketplaces={"bazos_cz": True, "sbazar_cz": True},
            error={},
        )
        session.add(rr777)
        session.flush()

        res1 = ResearchResult(
            id="rr-777-1", run_id=rr777.id, marketplace_key="bazos_cz",
            external_url="https://pc.bazos.cz/inzerat/223303840/lenovo-p14s-gen-5-ryzen-7-8840hs-163296gb-ramzar.php",
            title="Lenovo P14s gen 5 (Ryzen 7 8840HS, 32GB RAM, zár.)",
            price=Decimal("29800.00"), currency="CZK", price_base=Decimal("1192.00"),
            fx_rate_date=datetime.now(UTC).date(), condition_text="jako nový", raw={},
        )
        res2 = ResearchResult(
            id="rr-777-2", run_id=rr777.id, marketplace_key="bazos_cz",
            external_url="https://pc.bazos.cz/inzerat/223283761/lenovo-thinkpad-x13-gen-6-ultra-5-235u-zaruka-2028.php",
            title="Lenovo Thinkpad X13 gen 6 (Ultra 5, záruka 2028)",
            price=Decimal("26400.00"), currency="CZK", price_base=Decimal("1056.00"),
            fx_rate_date=datetime.now(UTC).date(), condition_text="nový", raw={},
        )
        session.add_all([res1, res2])

        # Listings for 777
        l777_1 = Listing(
            id="listing-777-bazos", item_code="777", marketplace_key="bazos_cz",
            external_id="bazos-223303840",
            external_url="https://pc.bazos.cz/inzerat/223303840/lenovo-p14s-gen-5-ryzen-7-8840hs-163296gb-ramzar.php",
            price=Decimal("29800.00"), currency="CZK", price_base=Decimal("1192.00"),
            status="ACTIVE", publish_evidence={"source": "bazos_live_sync"},
        )
        l777_2 = Listing(
            id="listing-777-sbazar", item_code="777", marketplace_key="sbazar_cz",
            external_id="sbazar-19482711",
            external_url="https://www.sbazar.cz/inzerat/19482711-lenovo-thinkpad-p14s",
            price=Decimal("29900.00"), currency="CZK", price_base=Decimal("1196.00"),
            status="ACTIVE", publish_evidence={"source": "sbazar_sync"},
        )
        session.add_all([l777_1, l777_2])

        # Audit events for 777
        record_audit_event(
            session,
            actor="AGENT",
            action="ITEM_CREATED",
            entity="item",
            entity_id="777",
            data={"source": "cli", "kind": "SELL", "title": it777.title},
        )
        record_audit_event(
            session,
            actor="AGENT",
            action="MEDIA_INGESTED",
            entity="item",
            entity_id="777",
            data={"photos_count": 3, "roles": ["ORIGINAL", "DEFECT", "PUBLISH"]},
        )
        record_audit_event(
            session,
            actor="SYSTEM",
            action="VLM_INSPECTION_PASSED",
            entity="item",
            entity_id="777",
            data={"model": "grok-vision-beta", "authentic": True, "defect_confirmed": True},
        )
        record_audit_event(
            session,
            actor="AGENT",
            action="MARKET_RESEARCH_COMPLETED",
            entity="item",
            entity_id="777",
            data={"platform": "bazos_cz", "median_czk": 29800, "base_eur": 1192.0},
        )
        record_audit_event(
            session,
            actor="AGENT",
            action="DRAFT_GENERATED",
            entity="item",
            entity_id="777",
            data={"target_price_eur": 1192.0, "platforms": ["bazos_cz", "sbazar_cz", "aukro_cz"]},
        )

        # ==========================================
        # ITEM 2: #802 (Apple iPhone 13 Pro 128GB)
        # ==========================================
        it802 = Item(
            code="802",
            kind="SELL",
            title="Apple iPhone 13 Pro 128GB Sierra Blue",
            category_key="electronics",
            attrs={
                "brand": "Apple",
                "model": "iPhone 13 Pro",
                "color": "Sierra Blue",
                "storage": "128 GB",
                "battery_health": "87%",
                "display": "Super Retina XDR 120Hz ProMotion",
                "accessories": "Коробка, кабель USB-C/Lightning",
                "pickup_allowed": True,
            },
            condition_key="used",
            defects={"bezel_scratch": "Небольшая царапина 3 мм на нижней стальной грани возле Lightning-разъема"},
            status=ItemStatus.APPROVED.value,
            target_price=Decimal("450.00"),
            target_currency="EUR",
            notes="VLM-контроль: дефект сохранен в комплекте публикации. Товар утвержден Владельцем.",
        )
        session.add(it802)
        session.flush()

        lot802 = Lot(
            id="lot-802",
            code="802",
            item_id="802",
            status=ItemStatus.APPROVED.value,
            price_gross=Decimal("15900.00"),
            price_floor=Decimal("14500.00"),
            currency="CZK",
            shipping_policy="BUYER_PAYS",
            notes="Apple iPhone 13 Pro 128GB",
        )
        session.add(lot802)

        p802_1_path = Path("data/media/802/originals/iphone_front.jpg").resolve()
        p802_2_path = Path("data/media/802/originals/iphone_defect.jpg").resolve()
        p802_3_path = Path("data/media/802/processed/iphone_publish.jpg").resolve()

        p802_1 = ItemPhoto(
            id="p-802-front", item_code="802", role="ORIGINAL",
            path=str(p802_1_path), sha256=compute_sha(p802_1_path), width=640, height=640,
            transform_log={"orientation": 1, "sanitized": True}, ai_generated=False,
            vlm_verdict={"authentic": True, "verdict": "REAL_PHOTO"},
        )
        p802_2 = ItemPhoto(
            id="p-802-defect", item_code="802", role="DEFECT",
            path=str(p802_2_path), sha256=compute_sha(p802_2_path), width=640, height=640,
            transform_log={"macro": True}, ai_generated=False,
            vlm_verdict={"authentic": True, "defect_confirmed": True, "note": "Царапина зафиксирована честно"},
        )
        p802_3 = ItemPhoto(
            id="p-802-publish", item_code="802", role="PUBLISH",
            path=str(p802_3_path), sha256=compute_sha(p802_3_path), width=640, height=640,
            transform_log={"studio": True}, ai_generated=True,
            vlm_verdict={"authentic": True, "defect_preserved": True, "note": "Дефект сохранен в публикации"},
        )
        session.add_all([p802_1, p802_2, p802_3])

        l802_1 = Listing(
            id="listing-802-bazos", item_code="802", marketplace_key="bazos_cz",
            external_id="bazos-223401923",
            external_url="https://mobil.bazos.cz/inzerat/223401923/apple-iphone-13-pro-128gb-sierra-blue.php",
            price=Decimal("11250.00"), currency="CZK", price_base=Decimal("450.00"),
            status="ACTIVE", publish_evidence={"source": "bazos_sync"},
        )
        session.add(l802_1)
        session.flush()

        # Thread and message for 802
        th802 = Thread(
            id="thread-802-1", listing_id=l802_1.id, channel="MARKETPLACE",
            counterparty="Petr Svoboda (Bazoš)", lang="cs", status="OPEN",
        )
        session.add(th802)
        session.flush()

        msg802 = Message(
            id="msg-802-1", thread_id=th802.id, direction="IN", transport="MARKETPLACE",
            text_original="Dobrý den, máte ještě ten iPhone 13 Pro? Je možné osobní předání v Praze?",
            lang_original="cs",
            text_ru="Добрый день, iPhone 13 Pro еще есть? Возможна ли личная встреча в Праге?",
            answered_by="AGENT", template_key="pickup_confirm", escalated=False,
            source_evidence={"raw": "bazos_msg_payload"},
        )
        session.add(msg802)

        # ==========================================
        # ITEM 3: #348 (Винтажный объектив Helios 44-2 58mm f/2)
        # ==========================================
        it348 = Item(
            code="348",
            kind="SELL",
            title="Helios 44-2 58mm f/2.0 (M42)",
            category_key="photo",
            attrs={
                "brand": "KMZ / Valdai",
                "mount": "M42",
                "focal_length": "58 mm",
                "max_aperture": "f/2.0",
                "blades": 8,
                "pickup_allowed": True,
            },
            condition_key="used",
            defects={"dust": "Единичные микропылинки внутри, без влияния на снимок"},
            status=ItemStatus.PUBLISHED.value,
            target_price=Decimal("60.00"),
            target_currency="EUR",
            notes="Активно продается на Aukro и Bazoš. Готов к отправке через Zásilkovna.",
        )
        session.add(it348)
        session.flush()

        lot348 = Lot(
            id="lot-348",
            code="348",
            item_id="348",
            status=ItemStatus.PUBLISHED.value,
            price_gross=Decimal("1850.00"),
            price_floor=Decimal("1600.00"),
            currency="CZK",
            shipping_policy="BUYER_PAYS",
            notes="Helios 44-2 58mm f/2.0",
        )
        session.add(lot348)

        p348_1_path = Path("data/media/348/originals/helios_front.jpg").resolve()
        p348_2_path = Path("data/media/348/processed/helios_publish.jpg").resolve()

        p348_1 = ItemPhoto(
            id="p-348-front", item_code="348", role="ORIGINAL",
            path=str(p348_1_path), sha256=compute_sha(p348_1_path), width=640, height=640,
            transform_log={"sanitized": True}, ai_generated=False,
            vlm_verdict={"authentic": True, "note": "Линзы чистые"},
        )
        p348_2 = ItemPhoto(
            id="p-348-publish", item_code="348", role="PUBLISH",
            path=str(p348_2_path), sha256=compute_sha(p348_2_path), width=640, height=640,
            transform_log={"studio": True}, ai_generated=True,
            vlm_verdict={"authentic": True, "note": "Студийный свет"},
        )
        session.add_all([p348_1, p348_2])

        l348_1 = Listing(
            id="listing-348-aukro", item_code="348", marketplace_key="aukro_cz",
            external_id="aukro-7058291044",
            external_url="https://aukro.cz/objektiv-helios-44-2-58mm-f2-m42-krasny-stav-7058291044",
            price=Decimal("1500.00"), currency="CZK", price_base=Decimal("60.00"),
            status="ACTIVE", publish_evidence={"source": "aukro_sync"},
        )
        session.add(l348_1)
        session.flush()

        deal348 = Deal(
            id="deal-348-1", listing_id=l348_1.id,
            agreed_price=Decimal("60.00"), currency="EUR", price_base=Decimal("60.00"),
            delivery_method="ZASILKOVNA", payment_method="ONLINE_PAYMENT",
            status="COMPLETED", closed_at=datetime.now(UTC),
            closure_evidence={"delivery_track": "Z491823901", "payout": "CONFIRMED"},
        )
        session.add(deal348)

        session.commit()
        print("Successfully seeded 3 realistic showcase items (#777, #802, #348) with authentic photos, live listings, inbox threads and deals!")

if __name__ == "__main__":
    seed()
