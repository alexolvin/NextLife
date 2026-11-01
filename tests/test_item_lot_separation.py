from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.services.lots import (
    LotServiceError,
    create_lot_for_item,
    relist_item,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Deal, Item, Listing, Lot, Marketplace, Message, Outbox, Thread


@pytest.fixture
def clean_db():
    from tests.conftest import clean_database
    from nextlife.connectors.registry import sync_marketplaces
    from nextlife.core.config import load_config
    with get_db_session() as session:
        sync_marketplaces(session, load_config())
        for mp in session.execute(select(Marketplace)).scalars():
            mp.enabled = (mp.access_mode == "DIRECT")
        session.commit()
        clean_database(session)
    yield
    with get_db_session() as session:
        sync_marketplaces(session, load_config())
        for mp in session.execute(select(Marketplace)).scalars():
            mp.enabled = (mp.access_mode == "DIRECT")
        session.commit()
        clean_database(session)


def test_item_lot_separation_and_relisting(clean_db):
    with get_db_session() as session:
        # 1. Physical item creation
        item = Item(
            code="348",
            kind="SELL",
            title="NVIDIA GeForce RTX 3090 24GB",
            category_key="gpu",
            condition_key="used",
            status="NEW",
            serial_number="SN-3090-998877",
        )
        session.add(item)
        session.commit()

        # 2. First lot creation (initial listing attempt)
        lot1 = create_lot_for_item(
            session=session,
            item_code="348",
            price_gross=Decimal("15000"),
            price_floor=Decimal("12000"),
            currency="CZK",
        )
        session.commit()

        assert lot1.code == "348"
        assert lot1.item_id == "348"
        assert lot1.price_gross == Decimal("15000")

        # Simulate lot1 expired / unsold / archived
        lot1.status = "ARCHIVED"
        session.commit()

        # 3. Relisting the same item creates a NEW lot with a NEW 3-digit code
        lot2 = relist_item(
            session=session,
            item_code="348",
            new_price_gross=Decimal("13500"),
            new_price_floor=Decimal("11000"),
            notes="Relisted with reduced price",
        )
        session.commit()

        assert lot2.item_id == "348"
        assert lot2.code != lot1.code
        assert len(lot2.code) == 3
        assert lot2.price_gross == Decimal("13500")
        assert lot2.status == "DRAFT"

        # 4. Check that item has both lots in history
        refreshed_item = session.get(Item, "348")
        assert len(refreshed_item.lots) == 2
        lot_codes = {lot.code for lot in refreshed_item.lots}
        assert lot1.code in lot_codes
        assert lot2.code in lot_codes


def test_relist_nonexistent_item_raises(clean_db):
    with get_db_session() as session:
        with pytest.raises(LotServiceError, match="Physical item '999' not found"):
            relist_item(
                session=session,
                item_code="999",
                new_price_gross=Decimal("1000"),
                new_price_floor=Decimal("800"),
            )


def test_derive_lot_currency_vinted_only(clean_db):
    """vinted only → CZK (ANDROID marketplace currency from config)."""
    from nextlife.services.lot_creation import derive_lot_currency

    with get_db_session() as session:
        mps = list(session.execute(select(Marketplace)).scalars())
        original_states = {mp.key: mp.enabled for mp in mps}
        for mp in mps:
            mp.enabled = mp.key == "vinted"
        session.commit()
        try:
            assert derive_lot_currency(session) == "CZK"
        finally:
            for mp in mps:
                mp.enabled = original_states[mp.key]
            session.commit()


def test_derive_lot_currency_vinted_bazos_same_currency(clean_db):
    """vinted (ANDROID, CZK) + bazos_cz (DIRECT, CZK) → CZK."""
    from nextlife.services.lot_creation import derive_lot_currency

    with get_db_session() as session:
        mps = list(session.execute(select(Marketplace)).scalars())
        original_states = {mp.key: mp.enabled for mp in mps}
        for mp in mps:
            mp.enabled = mp.key in ("vinted", "bazos_cz")
        session.commit()
        try:
            assert derive_lot_currency(session) == "CZK"
        finally:
            for mp in mps:
                mp.enabled = original_states[mp.key]
            session.commit()


def test_derive_lot_currency_mixed_currencies_raises(clean_db):
    """vinted (CZK) + kleinanzeigen_de (EUR) → error with marketplace list."""
    from nextlife.core.exceptions import InputError
    from nextlife.services.lot_creation import derive_lot_currency

    with get_db_session() as session:
        mps = list(session.execute(select(Marketplace)).scalars())
        original_states = {mp.key: mp.enabled for mp in mps}
        for mp in mps:
            mp.enabled = mp.key in ("vinted", "kleinanzeigen_de")
        session.commit()
        try:
            with pytest.raises(InputError, match="vinted=CZK"):
                derive_lot_currency(session)
        finally:
            for mp in mps:
                mp.enabled = original_states[mp.key]
            session.commit()
