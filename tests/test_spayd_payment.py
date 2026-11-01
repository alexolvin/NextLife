"""Tests for Czech SPAYD payment string and QR code generator (T-059, Section III.3.2).

DoD:
- String passes SPAYD format validation.
- Variable symbol (VS) is always equal to the 3-character lot code.
- Generates valid PNG QR code image.
- Bank card numbers are absent as a concept (forbidden by validation).
- Module length <= 400 lines.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from nextlife.payments.spayd import (
    SpaydError,
    generate_spayd_qr,
    generate_spayd_string,
    get_payment_details_for_lot,
    validate_spayd_string,
)

SAMPLE_IBAN = "CZ6508000000001920001453"


def test_generate_spayd_string_valid_format():
    lot_code = "039"
    amount = Decimal("4500.00")
    currency = "CZK"

    spayd = generate_spayd_string(
        iban=SAMPLE_IBAN,
        amount=amount,
        currency=currency,
        lot_code=lot_code,
    )

    expected = f"SPD*1.0*ACC:{SAMPLE_IBAN}*AM:4500.00*CC:CZK*X-VS:039*MSG:NL-039"
    assert spayd == expected

    # Validate parsing
    parsed = validate_spayd_string(spayd, expected_lot_code=lot_code)
    assert parsed["ACC"] == SAMPLE_IBAN
    assert parsed["AM"] == "4500.00"
    assert parsed["CC"] == "CZK"
    assert parsed["X-VS"] == lot_code
    assert parsed["MSG"] == "NL-039"


def test_variable_symbol_strictly_equals_lot_code():
    for code in ["001", "777", "802", "A12"]:
        spayd = generate_spayd_string(
            iban=SAMPLE_IBAN,
            amount=Decimal("199.90"),
            currency="CZK",
            lot_code=code,
        )
        parsed = validate_spayd_string(spayd, expected_lot_code=code)
        assert parsed["X-VS"] == code
        assert parsed["MSG"] == f"NL-{code}"


def test_validate_spayd_lot_code_mismatch_raises_error():
    spayd = generate_spayd_string(
        iban=SAMPLE_IBAN,
        amount=Decimal("1000.00"),
        currency="CZK",
        lot_code="039",
    )
    with pytest.raises(SpaydError, match="does not match expected lot code"):
        validate_spayd_string(spayd, expected_lot_code="999")


def test_invalid_spayd_parameters_raise_errors():
    # Negative / zero amount
    with pytest.raises(SpaydError, match="positive"):
        generate_spayd_string(SAMPLE_IBAN, Decimal("-100.00"), "CZK", "039")

    with pytest.raises(SpaydError, match="positive"):
        generate_spayd_string(SAMPLE_IBAN, Decimal("0.00"), "CZK", "039")

    # Invalid IBAN
    with pytest.raises(SpaydError, match="Invalid IBAN"):
        generate_spayd_string("INVALID_IBAN", Decimal("100.00"), "CZK", "039")

    # Invalid currency
    with pytest.raises(SpaydError, match="Invalid currency"):
        generate_spayd_string(SAMPLE_IBAN, Decimal("100.00"), "CZKKK", "039")

    # Missing header in validator
    with pytest.raises(SpaydError, match="must begin with 'SPD\\*1.0\\*'"):
        validate_spayd_string("ACC:CZ123*AM:100")


def test_credit_card_numbers_strictly_prohibited():
    """DoD: Bank card numbers cannot be accepted or injected."""
    fake_pan = "4111 2222 3333 4444"
    with pytest.raises(SpaydError, match="Invalid IBAN|card"):
        generate_spayd_string(fake_pan, Decimal("500.00"), "CZK", "039")


def test_generate_spayd_qr_image_bytes(tmp_path: Path):
    spayd = generate_spayd_string(
        iban=SAMPLE_IBAN,
        amount=Decimal("2999.00"),
        currency="CZK",
        lot_code="777",
    )

    qr_file = tmp_path / "test_spayd_qr.png"
    qr_bytes = generate_spayd_qr(spayd, output_path=qr_file)

    assert isinstance(qr_bytes, bytes)
    assert len(qr_bytes) > 100
    # PNG signature: \x89PNG\r\n\x1a\n
    assert qr_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert qr_file.exists()
    assert qr_file.stat().st_size == len(qr_bytes)


def test_get_payment_details_for_lot():
    details = get_payment_details_for_lot(
        lot_code="802",
        amount=Decimal("12500.00"),
        currency="CZK",
        iban=SAMPLE_IBAN,
    )

    assert details["iban"] == SAMPLE_IBAN
    assert details["amount"] == Decimal("12500.00")
    assert details["currency"] == "CZK"
    assert details["vs"] == "802"
    assert details["message"] == "NL-802"
    assert "SPD*1.0*" in details["spayd_string"]
    assert details["qr_bytes"][:8] == b"\x89PNG\r\n\x1a\n"
