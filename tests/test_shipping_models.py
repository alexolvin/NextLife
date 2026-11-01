from datetime import date
from decimal import Decimal

from nextlife.storage.db import get_db_session
from nextlife.storage.models import Broker, Carrier, DeliveryOption, Item, ShippingService


def test_item_dimensions_and_packaging_fields_separated():
    """Verify item dimensions and packaging fields are distinct attributes on Item model."""
    item = Item(
        code="991",
        kind="SELL",
        title="Test Hardware Item",
        category_key="hardware",
        attrs={},
        condition_key="used",
        defects={},
        status="DRAFT",
        # Item physical dimensions
        weight_g=1200,
        dim_l_mm=300,
        dim_w_mm=120,
        dim_h_mm=50,
        # Packaging dimensions (strictly separate)
        packed_weight_g=1600,
        packed_l_mm=380,
        packed_w_mm=200,
        packed_h_mm=100,
        fragile=True,
        hazardous=False,
        serial_number="SN-TEST-991",
        box_present=True,
        receipt_present=False,
        condition_grade="B",
    )
    assert item.weight_g == 1200
    assert item.packed_weight_g == 1600
    assert item.weight_g != item.packed_weight_g
    assert item.dim_l_mm != item.packed_l_mm
    assert item.fragile is True


def test_shipping_tables_db_crud():
    """Verify carrier, shipping_service, and delivery_option persistence in DB."""
    with get_db_session() as session:
        session.rollback()
        session.query(DeliveryOption).filter_by(item_code="992").delete()
        session.query(Item).filter_by(code="992").delete()
        session.query(ShippingService).filter_by(carrier_key="test_carrier").delete()
        session.query(Carrier).filter_by(key="test_carrier").delete()
        session.commit()
        try:
            # 1. Carrier
            carrier = Carrier(
                key="test_carrier",
                display_name="Test Logistics",
                source_url="https://example.com/rates",
            )
            session.add(carrier)
            session.commit()

            # 2. Service
            service = ShippingService(
                carrier_key="test_carrier",
                name="standard_box",
                service_type="Z-BOX",
                weight_max_g=5000,
                dim_rule={"max_sum_of_sides_mm": 1200},
                price=Decimal("79.00"),
                currency="CZK",
                cod_supported=True,
                cod_fee=Decimal("23.00"),
                countries=["CZ", "SK"],
                source_url="https://example.com/rates",
                fetched_at=date(2026, 9, 1),
            )
            session.add(service)
            session.commit()

            # 3. Item
            item = Item(
                code="992",
                kind="SELL",
                title="Test Item for Delivery",
                category_key="general",
                attrs={},
                condition_key="used",
                defects={},
                status="DRAFT",
                weight_g=500,
                packed_weight_g=700,
            )
            session.add(item)
            session.commit()

            # 4. Delivery Option
            opt = DeliveryOption(
                item_code="992",
                service_id=service.id,
                carrier_key="test_carrier",
                service_name="standard_box",
                price=Decimal("79.00"),
                is_manual_price=False,
                is_enabled=True,
            )
            session.add(opt)
            session.commit()

            # Verify fetch
            loaded_opt = session.query(DeliveryOption).filter_by(item_code="992").first()
            assert loaded_opt is not None
            assert loaded_opt.service_name == "standard_box"
            assert loaded_opt.price == Decimal("79.00")
            assert loaded_opt.service.carrier_key == "test_carrier"
        finally:
            session.query(DeliveryOption).filter_by(item_code="992").delete()
            session.query(Item).filter_by(code="992").delete()
            session.query(ShippingService).filter_by(carrier_key="test_carrier").delete()
            session.query(Carrier).filter_by(key="test_carrier").delete()
            session.commit()


def test_broker_and_shipping_service_keys_db_crud():
    """Verify Broker entity, broker_key, sender_on_documents, and claims_handler in DB."""
    with get_db_session() as session:
        session.rollback()
        session.query(ShippingService).filter_by(carrier_key="ppl_test").delete()
        session.query(Carrier).filter_by(key="ppl_test").delete()
        session.query(Broker).filter_by(key="delivera_test").delete()
        session.commit()
        try:
            # 1. Create carrier
            carrier = Carrier(
                key="ppl_test",
                display_name="PPL Test",
                source_url="https://example.com/ppl",
            )
            session.add(carrier)

            # 2. Create broker
            broker = Broker(
                key="delivera_test",
                display_name="Delivera Test",
                status="ACTIVE",
                sender_on_documents="BROKER",
                cod_payout_destination="BROKER_ACCOUNT",
                claims_handler="BROKER",
            )
            session.add(broker)
            session.commit()

            # 3. Create service via broker
            service = ShippingService(
                carrier_key="ppl_test",
                broker_key="delivera_test",
                name="ppl_via_delivera_test",
                service_type="výdejní místo",
                weight_max_g=10000,
                price=Decimal("69.00"),
                currency="CZK",
                sender_on_documents="BROKER",
                cod_payout_destination="BROKER_ACCOUNT",
                claims_handler="BROKER",
                insurance_limit=Decimal("20000.00"),
                source_url="https://example.com/delivera",
                fetched_at=date(2026, 9, 9),
            )
            session.add(service)
            session.commit()

            loaded_svc = session.query(ShippingService).filter_by(name="ppl_via_delivera_test").first()
            assert loaded_svc is not None
            assert loaded_svc.carrier_key == "ppl_test"
            assert loaded_svc.broker_key == "delivera_test"
            assert loaded_svc.sender_on_documents == "BROKER"
            assert loaded_svc.cod_payout_destination == "BROKER_ACCOUNT"
            assert loaded_svc.claims_handler == "BROKER"
            assert loaded_svc.insurance_limit == Decimal("20000.00")
            assert loaded_svc.broker.display_name == "Delivera Test"
            assert loaded_svc.carrier.display_name == "PPL Test"
        finally:
            session.query(ShippingService).filter_by(carrier_key="ppl_test").delete()
            session.query(Carrier).filter_by(key="ppl_test").delete()
            session.query(Broker).filter_by(key="delivera_test").delete()
            session.commit()

