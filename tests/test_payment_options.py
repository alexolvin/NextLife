"""Tests for payment options and threshold enforcement in lots (T-058, Section III.3.1).

DoD:
- Four payment methods: Cash on pickup, Bank transfer prepay, COD (Dobírka), Marketplace internal.
- Thresholds: cod_max_amount and prepay_required_above.
- COD strictly not offered above threshold.
- Boundary values verified: exact threshold, threshold - 0.01, threshold + 0.01.
- Module length <= 400 lines.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from nextlife.services.lot_creation import create_item_and_lot
from nextlife.services.payment_options import (
    PaymentMethodKey,
    evaluate_lot_payment_options,
    render_payment_options_summary,
    save_lot_payment_options,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Lot, PaymentOption


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def _create_lot_with_price(session, price: Decimal, shipping_policy: str = "BUYER_PAYS"):
    created = create_item_and_lot(
        session=session,
        title="Payment Test Item",
        category_key="general",
        price=price,
    )
    lot_code = created["lot_code"]
    lot = session.scalar(select(Lot).where(Lot.code == lot_code))
    lot.shipping_policy = shipping_policy
    session.flush()
    return lot


def test_four_payment_methods_present(db):
    lot = _create_lot_with_price(db, Decimal("2500.00"))
    opts = evaluate_lot_payment_options(
        lot,
        delivery_methods=["zasilkovna", "personal"],
        marketplace_key="aukro_cz",
        cod_supported_by_carrier=True,
    )

    methods = {o["payment_method"]: o for o in opts}
    assert PaymentMethodKey.CASH_ON_PICKUP.value in methods
    assert PaymentMethodKey.BANK_TRANSFER_PREPAY.value in methods
    assert PaymentMethodKey.COD.value in methods
    assert PaymentMethodKey.MARKETPLACE_INTERNAL.value in methods

    # Cash on pickup is available
    assert methods[PaymentMethodKey.CASH_ON_PICKUP.value]["is_available"] is True
    assert methods[PaymentMethodKey.CASH_ON_PICKUP.value]["confirmed_by"] == "OWNER"

    # Bank transfer prepay is always available
    assert methods[PaymentMethodKey.BANK_TRANSFER_PREPAY.value]["is_available"] is True
    assert methods[PaymentMethodKey.BANK_TRANSFER_PREPAY.value]["confirmed_by"] == "OWNER"

    # COD is available under threshold
    assert methods[PaymentMethodKey.COD.value]["is_available"] is True
    assert methods[PaymentMethodKey.COD.value]["confirmed_by"] == "OWNER"

    # Aukro internal payment is available
    assert methods[PaymentMethodKey.MARKETPLACE_INTERNAL.value]["is_available"] is True
    assert methods[PaymentMethodKey.MARKETPLACE_INTERNAL.value]["confirmed_by"] == "MARKETPLACE"


def test_cod_boundary_values_exact_threshold(db):
    """DoD: Verify boundary values for COD: below limit, exact limit, above limit (+0.01)."""
    threshold = Decimal("5000.00")

    # 1. Price is strictly below threshold (4999.99)
    lot_below = _create_lot_with_price(db, Decimal("4999.99"))
    opts_below = evaluate_lot_payment_options(
        lot_below,
        cod_max_amount=threshold,
        prepay_required_above=threshold,
    )
    cod_below = next(o for o in opts_below if o["payment_method"] == PaymentMethodKey.COD.value)
    assert cod_below["is_available"] is True
    assert cod_below["restriction_reason"] is None

    # 2. Price is EXACTLY at the threshold (5000.00)
    lot_exact = _create_lot_with_price(db, Decimal("5000.00"))
    opts_exact = evaluate_lot_payment_options(
        lot_exact,
        cod_max_amount=threshold,
        prepay_required_above=threshold,
    )
    cod_exact = next(o for o in opts_exact if o["payment_method"] == PaymentMethodKey.COD.value)
    assert cod_exact["is_available"] is True
    assert cod_exact["restriction_reason"] is None

    # 3. Price is ABOVE threshold by 0.01 (5000.01) -> MUST BE REJECTED
    lot_above = _create_lot_with_price(db, Decimal("5000.01"))
    opts_above = evaluate_lot_payment_options(
        lot_above,
        cod_max_amount=threshold,
        prepay_required_above=threshold,
    )
    cod_above = next(o for o in opts_above if o["payment_method"] == PaymentMethodKey.COD.value)
    assert cod_above["is_available"] is False
    assert "превышает" in cod_above["restriction_reason"]
    assert "Предоплата обязательна" in cod_above["restriction_reason"]

    # 4. Large price (15000.00) -> MUST BE REJECTED
    lot_large = _create_lot_with_price(db, Decimal("15000.00"))
    opts_large = evaluate_lot_payment_options(
        lot_large,
        cod_max_amount=threshold,
        prepay_required_above=threshold,
    )
    cod_large = next(o for o in opts_large if o["payment_method"] == PaymentMethodKey.COD.value)
    assert cod_large["is_available"] is False
    assert "Предоплата обязательна" in cod_large["restriction_reason"]


def test_carrier_cod_support_and_pickup_only_rules(db):
    threshold = Decimal("5000.00")

    # Carrier does not support COD
    lot = _create_lot_with_price(db, Decimal("1000.00"))
    opts = evaluate_lot_payment_options(
        lot,
        cod_supported_by_carrier=False,
        cod_max_amount=threshold,
    )
    cod = next(o for o in opts if o["payment_method"] == PaymentMethodKey.COD.value)
    assert cod["is_available"] is False
    assert "не поддерживает наложенный платёж" in cod["restriction_reason"]

    # Shipping policy is PICKUP_ONLY
    lot_pickup = _create_lot_with_price(db, Decimal("1000.00"), shipping_policy="PICKUP_ONLY")
    opts_pickup = evaluate_lot_payment_options(lot_pickup)
    cod_p = next(o for o in opts_pickup if o["payment_method"] == PaymentMethodKey.COD.value)
    assert cod_p["is_available"] is False
    assert "самовывоз" in cod_p["restriction_reason"]

    cash_p = next(o for o in opts_pickup if o["payment_method"] == PaymentMethodKey.CASH_ON_PICKUP.value)
    assert cash_p["is_available"] is True


def test_save_and_retrieve_lot_payment_options(db):
    lot = _create_lot_with_price(db, Decimal("3200.00"))
    opts = evaluate_lot_payment_options(
        lot,
        delivery_methods=["zasilkovna"],
        marketplace_key="bazos_cz",
    )

    saved = save_lot_payment_options(db, lot.id, opts)
    assert len(saved) == 4

    # Verify DB records
    records = db.scalars(
        select(PaymentOption).where(PaymentOption.lot_id == lot.id)
    ).all()
    assert len(records) == 4
    db_methods = {r.payment_method: r for r in records}

    assert db_methods[PaymentMethodKey.BANK_TRANSFER_PREPAY.value].is_available is True
    assert db_methods[PaymentMethodKey.COD.value].is_available is True
    # Bazos does not have internal escrow payment
    assert db_methods[PaymentMethodKey.MARKETPLACE_INTERNAL.value].is_available is False


def test_render_payment_options_summary(db):
    lot = _create_lot_with_price(db, Decimal("6500.00"))
    opts = evaluate_lot_payment_options(
        lot,
        cod_max_amount=Decimal("5000.00"),
        prepay_required_above=Decimal("5000.00"),
    )

    summary = render_payment_options_summary(opts, currency="CZK")
    assert "Способы оплаты" in summary
    assert "Банковский перевод заранее" in summary
    assert "Наложенный платёж (Dobírka): Недоступно" in summary
    assert "Предоплата обязательна" in summary
    assert "Банковские карты не запрашиваются" in summary
