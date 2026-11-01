"""Tests for Stage E2 (Деньги)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from nextlife.money.money import (
    UnknownCurrencyError,
    convert_price,
    format_amount,
    format_price_display,
    parse_ecb_xml,
)
from nextlife.storage.db import get_db_session
from nextlife.storage.models import FxRate


def test_format_amount() -> None:
    assert format_amount(Decimal("4500")) == "4 500"
    assert format_amount(Decimal("182.00")) == "182"
    assert format_amount(Decimal("182.50")) == "182,5"
    assert format_amount(Decimal("182.25")) == "182,25"


def test_czk_to_eur_conversion() -> None:
    rate_date = date(2026, 9, 5)
    rate_val = Decimal("24.7")  # 1 EUR = 24.7 CZK

    with get_db_session() as session:
        # Insert test rate
        fx = FxRate(
            rate_date=rate_date,
            currency="CZK",
            rate_to_base=rate_val,
            source="ECB",
            fetched_at=datetime.now(UTC),
        )
        session.merge(fx)
        session.flush()

        # Convert 4500 CZK -> should be 4500 / 24.7 = 182.186... -> 182.19 EUR
        price = convert_price(
            session=session,
            amount=Decimal("4500"),
            currency="CZK",
            target_date=rate_date,
            base_currency="EUR",
        )

        assert price.amount == Decimal("4500")
        assert price.currency == "CZK"
        assert price.amount_base == Decimal("182.19")
        assert price.rate_date == rate_date
        assert price.rate_to_base == rate_val

        # Check report formatting
        formatted = format_price_display(price, base_currency="EUR")
        assert "4 500 CZK ≈ 182,19 EUR (курс 24,7 от 2026-09-05)" == formatted

        session.rollback()


def test_missing_date_picks_closest_previous_rate() -> None:
    rate_date = date(2026, 9, 1)
    query_date = date(2026, 9, 5)
    rate_val = Decimal("25.0")

    with get_db_session() as session:
        fx = FxRate(
            rate_date=rate_date,
            currency="CZK",
            rate_to_base=rate_val,
            source="ECB",
            fetched_at=datetime.now(UTC),
        )
        session.merge(fx)
        session.flush()

        # Request on 2026-09-05 where only 2026-09-01 rate exists
        price = convert_price(
            session=session,
            amount=Decimal("5000"),
            currency="CZK",
            target_date=query_date,
            base_currency="EUR",
        )

        assert price.amount_base == Decimal("200.00")
        # Must reflect the actual date of the rate used (2026-09-01), NOT 2026-09-05
        assert price.rate_date == rate_date

        formatted = format_price_display(price, base_currency="EUR")
        assert "от 2026-09-01" in formatted
        assert "от 2026-09-05" not in formatted

        session.rollback()


def test_unknown_currency_raises_error() -> None:
    with get_db_session() as session:
        # None currency
        with pytest.raises(UnknownCurrencyError) as exc_info:
            convert_price(
                session=session,
                amount=Decimal("100"),
                currency=None,
                target_date=date(2026, 9, 5),
                base_currency="EUR",
            )
        assert "currency_unknown" in str(exc_info.value)

        # explicit currency_unknown
        with pytest.raises(UnknownCurrencyError):
            convert_price(
                session=session,
                amount=Decimal("100"),
                currency="currency_unknown",
                target_date=date(2026, 9, 5),
                base_currency="EUR",
            )


def test_parse_ecb_xml_sample() -> None:
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
        <Cube>
            <Cube time="2026-09-04">
                <Cube currency="USD" rate="1.0850"/>
                <Cube currency="CZK" rate="24.680"/>
                <Cube currency="PLN" rate="4.2810"/>
            </Cube>
        </Cube>
    </gesmes:Envelope>"""
    rates = parse_ecb_xml(sample_xml)
    assert len(rates) == 3
    czk = next(r for r in rates if r["currency"] == "CZK")
    assert czk["rate_date"] == date(2026, 9, 4)
    assert czk["rate_to_base"] == Decimal("24.680")
