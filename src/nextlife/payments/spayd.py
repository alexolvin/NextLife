"""Czech SPAYD (Short Payment Descriptor) and QR payment generator (TZ v2.0 Section III.3.2).

DoD:
- SPAYD format: SPD*1.0*ACC:<IBAN>*AM:<сумма>*CC:<валюта>*X-VS:<код лота>*MSG:NL-<код лота>.
- Variable Symbol (VS) strictly equals the 3-character lot code.
- Generates valid QR-code image for Czech banking apps.
- Bank card numbers are never stored, requested, or processed anywhere in the code.
- Module length <= 400 lines.
"""

import io
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import qrcode  # type: ignore[import-untyped]

from nextlife.core.config import load_config
from nextlife.core.exceptions import NextLifeError

# Regex for basic IBAN validation (2 letters country code, 2 check digits, up to 30 alphanumeric)
IBAN_REGEX = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$")

# Card number check pattern (strictly forbidden)
CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


class SpaydError(NextLifeError):
    """Base error for SPAYD formatting or generation."""


def generate_spayd_string(
    iban: str,
    amount: Decimal,
    currency: str,
    lot_code: str,
) -> str:
    """Generate SPAYD string conforming to Czech banking standard.

    DoD:
    - Format: SPD*1.0*ACC:<IBAN>*AM:<сумма>*CC:<валюта>*X-VS:<код лота>*MSG:NL-<код лота>.
    - X-VS is always set to the lot_code.
    - Card numbers never accepted or processed.
    """
    clean_iban = re.sub(r"\s+", "", iban.strip().upper())
    if not IBAN_REGEX.match(clean_iban):
        raise SpaydError(f"Invalid IBAN format: '{iban}'")

    if amount <= Decimal("0.00"):
        raise SpaydError(f"Payment amount must be positive, got {amount}")

    clean_currency = currency.strip().upper()
    if len(clean_currency) != 3 or not clean_currency.isalpha():
        raise SpaydError(f"Invalid currency code: '{currency}'")

    clean_lot_code = str(lot_code).strip()
    if not clean_lot_code:
        raise SpaydError("Lot code cannot be empty")

    # Guard against accidental card number passing
    if CARD_PATTERN.search(clean_iban) and not clean_iban.startswith(("CZ", "SK", "DE", "AT")):
        raise SpaydError("Potential card number detected in IBAN field. Cards strictly prohibited.")

    formatted_amount = f"{amount:.2f}"
    return (
        f"SPD*1.0*ACC:{clean_iban}"
        f"*AM:{formatted_amount}"
        f"*CC:{clean_currency}"
        f"*X-VS:{clean_lot_code}"
        f"*MSG:NL-{clean_lot_code}"
    )


def validate_spayd_string(spayd_string: str, expected_lot_code: str | None = None) -> dict[str, str]:
    """Validate SPAYD string structure and verify field correctness.

    DoD:
    - String passes format validation.
    - VS matches lot_code.
    - No card details present.
    """
    if not spayd_string.startswith("SPD*1.0*"):
        raise SpaydError("SPAYD string must begin with 'SPD*1.0*'")

    # Split tokens after the header
    tokens = spayd_string[8:].split("*")
    data: dict[str, str] = {}
    for token in tokens:
        if not token:
            continue
        if ":" not in token:
            raise SpaydError(f"Invalid SPAYD token format (missing colon): '{token}'")
        k, v = token.split(":", 1)
        data[k] = v

    required_keys = {"ACC", "AM", "CC", "X-VS", "MSG"}
    missing = required_keys - set(data.keys())
    if missing:
        raise SpaydError(f"SPAYD string missing required fields: {missing}")

    # Validate IBAN in ACC
    if not IBAN_REGEX.match(data["ACC"]):
        raise SpaydError(f"Invalid IBAN in SPAYD ACC: '{data['ACC']}'")

    # Validate amount
    try:
        amt = Decimal(data["AM"])
        if amt <= Decimal("0.00"):
            raise SpaydError(f"Non-positive amount in SPAYD: {amt}")
    except Exception as exc:
        raise SpaydError(f"Failed to parse amount in SPAYD AM: {exc}") from exc

    # Validate VS
    if expected_lot_code is not None and data["X-VS"] != str(expected_lot_code):
        raise SpaydError(f"Variable symbol '{data['X-VS']}' does not match expected lot code '{expected_lot_code}'")

    # Validate message
    expected_msg = f"NL-{data['X-VS']}"
    if data["MSG"] != expected_msg:
        raise SpaydError(f"Message '{data['MSG']}' does not match expected format '{expected_msg}'")

    # Verify absence of credit card numbers anywhere in the SPAYD string
    if CARD_PATTERN.search(spayd_string):
        raise SpaydError("Credit card number pattern detected in SPAYD string!")

    return data


def generate_spayd_qr(
    spayd_string: str,
    output_path: Path | None = None,
) -> bytes:
    """Generate PNG QR code image for a SPAYD payment string."""
    validate_spayd_string(spayd_string)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(spayd_string)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(png_bytes)

    return png_bytes


def get_payment_details_for_lot(
    lot_code: str,
    amount: Decimal,
    currency: str = "CZK",
    iban: str | None = None,
    output_qr_path: Path | None = None,
) -> dict[str, Any]:
    """Assemble complete prepayment package with IBAN, VS, SPAYD string, and QR code."""
    cfg = load_config()
    target_iban: str = str(iban or getattr(cfg.app, "owner_iban", "CZ6508000000001920001453") or "CZ6508000000001920001453")

    spayd_str = generate_spayd_string(
        iban=target_iban,
        amount=amount,
        currency=currency,
        lot_code=lot_code,
    )

    qr_bytes = generate_spayd_qr(spayd_str, output_path=output_qr_path)

    return {
        "iban": target_iban,
        "amount": amount,
        "currency": currency,
        "vs": lot_code,
        "message": f"NL-{lot_code}",
        "spayd_string": spayd_str,
        "qr_bytes": qr_bytes,
    }
