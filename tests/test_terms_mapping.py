"""Tests for marketplace terms mapping and capability enforcement (T-047).

DoD:
- Delivery and payment options are mapped to marketplace-specific fields.
- Unsupported delivery or payment options raise CapabilityNotSupported and cannot be silently published.
- Module length <= 400 lines.
"""

from decimal import Decimal

import pytest

from nextlife.connectors.base import CapabilityNotSupported
from nextlife.connectors.terms_mapping import (
    map_lot_terms_to_marketplace,
    map_terms_to_marketplace,
)
from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.shipping_options import configure_lot_delivery_options
from nextlife.storage.db import get_db_session
from nextlife.storage.models import DeliveryOption


def test_successful_terms_mapping():
    # 1. Bazos mapping
    b_res = map_terms_to_marketplace(
        marketplace_key="bazos_cz",
        delivery_options=[
            {"carrier_key": "pickup", "price": 0.0},
            {"carrier_key": "cpost", "price": 109.0},
        ],
        payment_options=["cash", "bank_transfer"],
        shipping_policy="BUYER_PAYS",
    )
    assert b_res.marketplace_key == "bazos_cz"
    delivery_fields = [d["marketplace_field"] for d in b_res.mapped_delivery]
    assert "Osobní předání" in delivery_fields
    assert "Česká pošta" in delivery_fields
    payment_fields = [p["marketplace_field"] for p in b_res.mapped_payments]
    assert "Hotově při předání" in payment_fields
    assert "Převodem na účet" in payment_fields
    assert "Doprava: Osobní předání, Česká pošta" in b_res.human_summary_cz

    # 2. Sbazar mapping
    s_res = map_terms_to_marketplace(
        marketplace_key="sbazar_cz",
        delivery_options=["pickup", "zasilkovna"],
        payment_options=["cash", "cod"],
        shipping_policy="BUYER_PAYS",
    )
    s_del_fields = [d["marketplace_field"] for d in s_res.mapped_delivery]
    assert "Osobní odběr" in s_del_fields
    assert "Zásilkovna" in s_del_fields
    s_pay_fields = [p["marketplace_field"] for p in s_res.mapped_payments]
    assert "Hotově" in s_pay_fields
    assert "Dobírka" in s_pay_fields

    # 3. Aukro mapping
    a_res = map_terms_to_marketplace(
        marketplace_key="aukro_cz",
        delivery_options=["courier", "balikovna"],
        payment_options=["aukro_safe", "bank_transfer"],
        shipping_policy="SELLER_PAYS",
    )
    a_del_fields = [d["marketplace_field"] for d in a_res.mapped_delivery]
    assert "Kurýrní služba" in a_del_fields
    assert "Balíkovna" in a_del_fields
    a_pay_fields = [p["marketplace_field"] for p in a_res.mapped_payments]
    assert "Bezpečná platba přes Aukro" in a_pay_fields


def test_unsupported_delivery_option_raises_capability_not_supported():
    # Bazos does NOT support courier (only pickup, post, cpost)
    with pytest.raises(CapabilityNotSupported) as exc_info:
        map_terms_to_marketplace(
            marketplace_key="bazos_cz",
            delivery_options=["courier"],
            payment_options=["cash"],
        )
    err = str(exc_info.value)
    assert "courier" in err
    assert "Bazoš.cz" in err
    assert "Option cannot be published silently" in err


def test_unsupported_payment_option_raises_capability_not_supported():
    # Bazos does NOT support aukro_safe or crypto
    with pytest.raises(CapabilityNotSupported) as exc_info:
        map_terms_to_marketplace(
            marketplace_key="bazos_cz",
            delivery_options=["pickup"],
            payment_options=["crypto"],
        )
    err = str(exc_info.value)
    assert "crypto" in err
    assert "Bazoš.cz" in err
    assert "Option cannot be published silently" in err


def test_map_lot_terms_from_database_success_and_failure():
    with get_db_session() as session:
        created = create_item_and_lot(
            session=session,
            title="Terms DB Test Item",
            category_key="general",
            price=Decimal("1500.00"),
        )
        lot_code = created["lot_code"]
        item_code = created["item_code"]

        # Configure delivery options supported by sbazar: pickup and zasilkovna
        configure_lot_delivery_options(
            session=session,
            lot_code=lot_code,
            options=[
                {"carrier_key": "pickup", "price": 0.0},
                {"carrier_key": "zasilkovna", "price": 79.0},
            ],
        )
        session.commit()

        # Sbazar supports both -> success
        mapped = map_lot_terms_to_marketplace(
            session=session,
            lot_code=lot_code,
            marketplace_key="sbazar_cz",
        )
        assert len(mapped.mapped_delivery) == 2
        fields = [d["marketplace_field"] for d in mapped.mapped_delivery]
        assert "Zásilkovna" in fields

        # Now configure unsupported carrier for bazos (e.g. balikovna)
        configure_lot_delivery_options(
            session=session,
            lot_code=lot_code,
            options=[
                {"carrier_key": "balikovna", "price": 65.0},
            ],
        )
        session.commit()

        # Bazos mapping must fail with CapabilityNotSupported
        with pytest.raises(CapabilityNotSupported) as exc_info:
            map_lot_terms_to_marketplace(
                session=session,
                lot_code=lot_code,
                marketplace_key="bazos_cz",
            )
        assert "balikovna" in str(exc_info.value)

        # Clean up
        session.query(DeliveryOption).filter(DeliveryOption.item_code == item_code).delete()
        session.commit()
