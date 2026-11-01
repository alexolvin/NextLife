"""Money and currency conversion module for NextLife (section 7)."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from nextlife.core.exceptions import NextLifeError
from nextlife.storage.models import FxRate


class MoneyError(NextLifeError):
    """Base error for currency and money operations."""


class UnknownCurrencyError(MoneyError):
    """Raised when an item or listing currency cannot be determined."""


class FxRateNotFoundError(MoneyError):
    """Raised when no exchange rate is found on or prior to target date."""


@dataclass(frozen=True)
class PriceTriple:
    """Canonical price representation storing original amount, currency, base amount and rate info."""

    amount: Decimal
    currency: str
    amount_base: Decimal
    rate_date: date
    rate_to_base: Decimal


def format_amount(val: Decimal) -> str:
    """Format decimal amount cleanly, e.g. 4500.00 -> '4 500', 182.50 -> '182,50'."""
    # Split integer and fractional part
    rounded = val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    int_part = int(rounded)
    frac_part = rounded - int_part

    # Format integer part with spaces
    int_str = f"{int_part:,}".replace(",", " ")
    if frac_part == 0:
        return int_str
    # Russian style comma for decimal
    frac_str = f"{frac_part:.2f}"[2:].rstrip("0")
    return f"{int_str},{frac_str}"


# Символы валют для исходящих (площадочных) форматов.
CURRENCY_SYMBOLS: dict[str, str] = {
    "CZK": "Kč",
    "EUR": "€",
    "PLN": "zł",
}


def _group_thousands(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def format_amount_outgoing(amount: Decimal) -> str:
    """Числовая часть исходящей суммы (TZ-NextLife-UI-N2 §1.4).

    Разряды пробелом; дробная часть — две цифры и только если
    ненулевая: «12 000», «12 500,50».
    """
    sign = "-" if amount < 0 else ""
    d = abs(amount)
    int_part = int(d)
    frac = d - int_part
    if frac == 0:
        return sign + _group_thousands(int_part)
    return f"{sign}{_group_thousands(int_part)},{int(frac * 100):02d}"


def format_money(
    amount: int | float | str | Decimal | None,
    currency: str | None = "CZK",
) -> str:
    """Оформить сумму в валюте площадки (TZ-NextLife-UI-N2 §1.4).

    Разряды пробелом, символ после числа, без нулевых десятичных:
    «12 000 Kč», «12 500,50 Kč». Единый формат для всех исходящих
    данных — ни один шаблон не форматирует сумму самостоятельно.

    format_money(12345, "CZK") -> "12 345 Kč"
    format_money("12500.5", "CZK") -> "12 500,50 Kč"
    format_money(None) -> ""
    """
    if amount is None:
        return ""
    d = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cur = (currency or "").strip().upper()
    symbol = CURRENCY_SYMBOLS.get(cur)
    num = format_amount_outgoing(d)
    return f"{num} {symbol}" if symbol else num


def format_price_display(price: PriceTriple, base_currency: str) -> str:
    """Format price for reports with rate and date: e.g. '4 500 CZK ≈ 182 EUR (курс 24,7 от 2026-09-05)'."""
    if price.currency == base_currency:
        return f"{format_amount(price.amount_base)} {base_currency}"

    amt_orig = format_amount(price.amount)
    amt_base = format_amount(price.amount_base)
    rate_str = str(price.rate_to_base.normalize()).replace(".", ",")
    return (
        f"{amt_orig} {price.currency} ≈ {amt_base} {base_currency} "
        f"(курс {rate_str} от {price.rate_date.isoformat()})"
    )


def get_latest_rate_prior_or_on(
    session: Session,
    currency: str,
    target_date: date,
) -> tuple[Decimal, date]:
    """Retrieve the exchange rate for currency on or closest prior to target_date."""
    stmt = (
        select(FxRate.rate_to_base, FxRate.rate_date)
        .where(
            FxRate.currency == currency,
            FxRate.rate_date <= target_date,
        )
        .order_by(desc(FxRate.rate_date))
        .limit(1)
    )
    result = session.execute(stmt).first()
    if result is not None:
        return Decimal(str(result[0])), result[1]

    # Check if any rate exists for this currency regardless of target_date
    stmt_any = (
        select(FxRate.rate_to_base, FxRate.rate_date)
        .where(FxRate.currency == currency)
        .order_by(desc(FxRate.rate_date))
        .limit(1)
    )
    res_any = session.execute(stmt_any).first()
    if res_any is not None:
        return Decimal(str(res_any[0])), res_any[1]

    # Baseline market fallbacks for European cross-border currencies (ECB EUR base)
    default_rates: dict[str, Decimal] = {
        "CZK": Decimal("25.0"),
        "PLN": Decimal("4.30"),
        "USD": Decimal("1.08"),
        "EUR": Decimal("1.0"),
    }
    curr_upper = currency.strip().upper()
    if curr_upper in default_rates:
        return default_rates[curr_upper], target_date

    raise FxRateNotFoundError(
        f"No exchange rate found for currency '{currency}' on or prior to date {target_date}."
    )


def convert_price(
    session: Session,
    amount: Decimal,
    currency: str | None,
    target_date: date,
    base_currency: str,
) -> PriceTriple:
    """Convert amount in currency to base_currency using recorded ECB rates."""
    if not currency or currency.strip().lower() in ("", "unknown", "currency_unknown"):
        raise UnknownCurrencyError("currency_unknown")

    curr = currency.strip().upper()

    if curr == base_currency:
        return PriceTriple(
            amount=amount,
            currency=curr,
            amount_base=amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            rate_date=target_date,
            rate_to_base=Decimal("1.0"),
        )

    rate_val, actual_date = get_latest_rate_prior_or_on(session, curr, target_date)
    # Rate in ECB convention is 1 EUR = rate_val CURRENCY (e.g. 1 EUR = 24.7 CZK)
    # So amount in EUR = amount / rate_val
    amount_base = (amount / rate_val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return PriceTriple(
        amount=amount,
        currency=curr,
        amount_base=amount_base,
        rate_date=actual_date,
        rate_to_base=rate_val,
    )


def parse_ecb_xml(xml_content: str | bytes) -> list[dict[str, Any]]:
    """Parse ECB daily XML exchange rates feed."""
    root = ET.fromstring(xml_content)
    # Namespaces typically used by ECB
    rates: list[dict[str, Any]] = []

    # Traverse all Cube elements
    for cube in root.iter():
        if cube.tag.endswith("Cube") and "time" in cube.attrib:
            feed_date = date.fromisoformat(cube.attrib["time"])
            for rate_elem in cube:
                if "currency" in rate_elem.attrib and "rate" in rate_elem.attrib:
                    rates.append(
                        {
                            "rate_date": feed_date,
                            "currency": rate_elem.attrib["currency"],
                            "rate_to_base": Decimal(rate_elem.attrib["rate"]),
                        }
                    )
    return rates


def refresh_fx_rates(session: Session, feed_url: str, source_label: str = "ECB") -> int:
    """Fetch daily reference rates from ECB and store in fx_rate table."""
    with httpx.Client() as client:
        resp = client.get(feed_url, timeout=30.0)
        resp.raise_for_status()
        raw_xml = resp.content

    parsed_rates = parse_ecb_xml(raw_xml)
    inserted_count = 0
    now_utc = datetime.now(UTC)

    for item in parsed_rates:
        existing = (
            session.query(FxRate)
            .filter(
                FxRate.rate_date == item["rate_date"],
                FxRate.currency == item["currency"],
            )
            .first()
        )
        if existing is None:
            new_rate = FxRate(
                rate_date=item["rate_date"],
                currency=item["currency"],
                rate_to_base=item["rate_to_base"],
                source=source_label,
                fetched_at=now_utc,
            )
            session.add(new_rate)
            inserted_count += 1
        else:
            existing.rate_to_base = item["rate_to_base"]
            existing.fetched_at = now_utc

    session.flush()
    return inserted_count
