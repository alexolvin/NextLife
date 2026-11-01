"""Tests for Handover Protocol Generator (TZ v2.0 Section III.5.3 / Task T-065).

DoD:
- Generates bilingual document (RU/CS) with all required fields:
  parties, date/place, item, serial number, defects list, price,
  buyer inspection confirmation, accessories completeness, signatures.
- Persisted to handover.protocol_path and deal evidence.
- Verified by outgoing filter.
"""

import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.deals.handover_protocol import (
    HandoverProtocolError,
    generate_handover_protocol,
    get_handover_protocol,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Handover, Item, ItemDefect, Lot, Party


def test_handover_protocol_contains_all_mandatory_fields_and_defects():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="GeForce RTX 4080 Gaming",
            category_key="gpu",
            price=Decimal("22000.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Set serial number and condition on item
        item = session.query(Item).filter(Item.code == item_code).first()
        assert item is not None
        item.serial_number = "SN-RTX4080-998877"
        item.condition_grade = "GOOD"
        item.title_cs = "Grafická karta GeForce RTX 4080"
        item.title_ru = "Видеокарта GeForce RTX 4080"

        # Add defect to ItemDefect
        defect = ItemDefect(
            id=str(uuid.uuid4()),
            item_code=item_code,
            defect_type="scratch",
            severity="cosmetic",
            description_owner="Небольшая царапина на бэкплейте",
            description_cs="Drobný škrábanec na backplate",
            photo_id="photo_defect_01",
            crop_box={"x": 10, "y": 10, "width": 50, "height": 50},
        )
        session.add(defect)

        # Create counterparty
        party = Party(
            id=str(uuid.uuid4()),
            name="Jan Novák",
            email="jan.novak@example.cz",
            phone="+420777112233",
        )
        session.add(party)

        # Create deal
        lot = session.query(Lot).filter(Lot.code == lot_code).first()
        deal = Deal(
            id=str(uuid.uuid4()),
            lot_id=lot.id,
            party_id=party.id,
            agreed_price=Decimal("21500.00"),
            currency="CZK",
            price_base=Decimal("21500.00"),
            delivery_method="PERSONAL_PICKUP",
            payment_method="CASH_ON_PICKUP",
            status="RESERVED",
        )
        session.add(deal)
        session.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            rendered, file_path = generate_handover_protocol(
                session=session,
                deal_id=deal.id,
                place="OC Palladium, Praha",
                seller_name="NextLife Admin",
                base_storage_dir=tmp_path,
                actor="TEST_ACTOR",
            )
            session.commit()

            # 1. Check mandatory fields
            assert "АКТ ПРИЁМА-ПЕРЕДАЧИ ТОВАРА / PŘEDÁVACÍ PROTOKOL" in rendered
            assert f"[{lot_code}]" in rendered
            assert "OC Palladium, Praha" in rendered
            assert "NextLife Admin" in rendered
            assert "Jan Novák" in rendered
            assert "SN-RTX4080-998877" in rendered, "Document must contain serial number"
            assert "Drobný škrábanec na backplate" in rendered, "Document must list defects"
            assert "Небольшая царапина на бэкплейте" in rendered
            assert "21500.00 CZK" in rendered
            assert "CASH_ON_PICKUP" in rendered
            assert "Отметка о проверке покупателем" in rendered
            assert "Potvrzení o vyzkoušení kupujícím" in rendered
            assert "ПОДПИСИ СТОРОН / PODPISY STRAN" in rendered

            # 2. Check file persistence
            assert file_path.exists()
            assert file_path.read_text(encoding="utf-8") == rendered

            # 3. Check handover record and evidence
            handover = session.query(Handover).filter(Handover.deal_id == deal.id).first()
            assert handover is not None
            assert handover.protocol_path == str(file_path)
            assert handover.evidence.get("serial_number") == "SN-RTX4080-998877"
            assert handover.evidence.get("defects_count") == 1

            # 4. Check get_handover_protocol
            retrieved = get_handover_protocol(session, deal.id)
            assert retrieved == rendered

        # Clean up
        session.query(Handover).filter(Handover.deal_id == deal.id).delete()
        session.query(Deal).filter(Deal.id == deal.id).delete()
        session.query(Party).filter(Party.id == party.id).delete()
        session.query(ItemDefect).filter(ItemDefect.item_code == item_code).delete()
        session.query(Lot).filter(Lot.code == lot_code).delete()
        session.query(Item).filter(Item.code == item_code).delete()
        session.commit()


def test_handover_protocol_nonexistent_deal_raises_error():
    with get_db_session() as session:
        with pytest.raises(HandoverProtocolError):
            generate_handover_protocol(session, deal_id="nonexistent-deal-uuid-123")
