"""Carrier and shipping service schemas (TZ v2.0 Section III.2.1)."""

import re
import unicodedata
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from nextlife.core.exceptions import NextLifeError


class ShippingValidationError(NextLifeError):
    """Raised when shipping configuration fails schema or honesty validation."""


class CarrierName(StrEnum):
    PACKETA = "Packeta"
    BALIKOVNA = "Balíkovna"
    CESKA_POSTA = "CeskaPosta"
    PPL = "PPL"
    DPD = "DPD"
    GLS = "GLS"
    PERSONAL = "Personal"


class DimensionRule(BaseModel):
    max_sum_of_sides_mm: int | None = None
    max_single_side_mm: int | None = None
    max_l_mm: int | None = None
    max_w_mm: int | None = None
    max_h_mm: int | None = None


class SizeClass(BaseModel):
    name: str = Field(..., description="Size class identifier (e.g. standard, nadrozmerna, S, M, L, XL)")
    max_weight_kg: Decimal | float | None = Field(default=None, description="Maximum package weight in kg")
    max_longest_side_cm: Decimal | float | None = Field(default=None, description="Maximum longest side in cm")
    max_sum_sides_cm: Decimal | float | None = Field(default=None, description="Maximum sum of sides in cm")
    box_max_dimensions_cm: list[float] | None = Field(
        default=None, description="Specific box max dimensions [H, W, L] in cm, e.g. for Z-BOX [35.0, 43.0, 60.0]"
    )


class PriceModifier(BaseModel):
    name: str = Field(..., description="Modifier name or promotion label")
    modifier_type: str = Field(default="FIXED_DISCOUNT", description="FIXED_DISCOUNT | PERCENT_DISCOUNT | SURCHARGE")
    amount: Decimal = Field(..., ge=0, description="Discount amount or percentage")
    valid_from: date = Field(..., description="Start date of modifier validity")
    valid_to: date = Field(..., description="End date of modifier validity")
    promo_code: str | None = Field(default=None, description="Promo code required to activate")

    def is_active(self, on_date: date | None = None) -> bool:
        target = on_date or date.today()
        return self.valid_from <= target <= self.valid_to

    def apply(self, base_price: Decimal, on_date: date | None = None) -> Decimal:
        if not self.is_active(on_date):
            return base_price
        if self.modifier_type == "PERCENT_DISCOUNT":
            return max(Decimal("0.0"), base_price * (Decimal("1.0") - (self.amount / Decimal("100.0"))))
        elif self.modifier_type == "SURCHARGE":
            return base_price + self.amount
        return max(Decimal("0.0"), base_price - self.amount)


class PdfPriceAnchor(BaseModel):
    """Table-cell address of a single price inside its source PDF.

    The three fields locate one price exactly: ``pdf_section`` (a verbatim block
    name occurring exactly once on the page), ``pdf_row`` (the row label: the first
    line after the section line that contains it), and ``pdf_column`` (the 0-based
    index of the price in that row, left to right). Section and row are compared
    ignoring whitespace; the price is read from the whitespace-normalized row so
    thousand separators survive the price pattern.
    """

    pdf_section: str = Field(..., min_length=1, description="Verbatim block/section name (unique on the page, matched ignoring spaces)")
    pdf_row: str = Field(..., min_length=1, description="Row label; first line after the section line containing it (matched ignoring spaces)")
    pdf_column: int = Field(..., ge=0, description="0-based index of the price in that row line, left to right")


class ShippingService(BaseModel):
    name: str = Field(..., description="Service identifier or name")
    service_type: str = Field(..., description="výdejní místo | Z-BOX | na adresu | почтовое отделение | osobní odběr")
    weight_max_g: int = Field(..., gt=0, description="Maximum package weight in grams")
    dim_rule: DimensionRule = Field(default_factory=DimensionRule)
    size_class: SizeClass | None = Field(
        default=None, description="Physical size class defined by weight, longest side, sum of sides"
    )
    price: Decimal = Field(..., ge=0, description="Base service price in currency")
    country_pricing: dict[str, Decimal] = Field(
        default_factory=dict, description="Pricing per destination country code (e.g. CZ, SK, HU, RO)"
    )
    price_pdf: PdfPriceAnchor | None = Field(
        default=None, description="Table-cell anchor in the source PDF for the base price"
    )
    country_pricing_pdf: dict[str, PdfPriceAnchor] | None = Field(
        default=None, description="Table-cell anchor in the source PDF for each destination-country price"
    )
    currency: str = Field(default="CZK", description="Currency code (e.g. CZK, EUR)")
    cod_supported: bool = Field(default=False, description="Whether Cash on Delivery (dobírka) is supported")
    cod_fee: Decimal = Field(default=Decimal("0.0"), ge=0, description="Additional fee for dobírka")
    dobirka_fee: Decimal = Field(default=Decimal("0.0"), ge=0, description="Explicit fee for dobírka")
    dobirka_limit: Decimal | None = Field(default=None, ge=0, description="Maximum allowed dobírka amount")
    unregistered_surcharge: Decimal = Field(
        default=Decimal("0.0"), ge=0, description="Surcharge for unregistered customers"
    )
    insurance_included: Decimal = Field(default=Decimal("0.0"), ge=0, description="Included insurance amount")
    insurance_limit: Decimal = Field(default=Decimal("0.0"), ge=0, description="Included insurance coverage limit")
    insurance_max: Decimal | None = Field(default=None, ge=0, description="Maximum possible insurance coverage")
    countries: list[str] = Field(default_factory=lambda: ["CZ"], description="Supported ISO country codes")
    delivery_days: str | int | None = Field(default=None, description="Estimated delivery days")
    broker_key: str | None = Field(default=None, description="Broker key if offered via intermediary (e.g. 'delivera')")
    carrier_key: str | None = Field(default=None, description="Underlying carrier identifier (e.g. 'ppl')")
    sender_on_documents: str = Field(default="SELLER", description="Who is sender on documents (SELLER | BROKER)")
    cod_payout_destination: str = Field(
        default="SELLER_BANK_ACCOUNT", description="Destination of COD payment (SELLER_BANK_ACCOUNT | BROKER_ACCOUNT)"
    )
    claims_handler: str = Field(default="CARRIER", description="Who handles/accepts claims (CARRIER | BROKER)")
    price_modifiers: list[PriceModifier] = Field(default_factory=list, description="Separate modifiers and promotions")
    source_file: str = Field(..., min_length=1, description="Path to official PDF source in config/shipping/_sources/")
    source_page: int = Field(..., ge=1, description="1-indexed page number in the source PDF")
    source_url: str = Field(..., min_length=8, description="Mandatory source URL of official tariff")
    fetched_at: date = Field(..., description="Date when tariff was officially verified")

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_url cannot be empty or null")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"source_url must be a valid HTTP/HTTPS URL, got '{v}'")
        return v.strip()

    @model_validator(mode="after")
    def sync_legacy_and_explicit_fields(self) -> "ShippingService":
        if self.dobirka_fee > Decimal("0.0"):
            self.cod_supported = True
            if self.cod_fee == Decimal("0.0"):
                self.cod_fee = self.dobirka_fee
        elif self.cod_fee > Decimal("0.0"):
            self.cod_supported = True
            if self.dobirka_fee == Decimal("0.0"):
                self.dobirka_fee = self.cod_fee

        if self.insurance_limit > Decimal("0.0") and self.insurance_included == Decimal("0.0"):
            self.insurance_included = self.insurance_limit
        elif self.insurance_included > Decimal("0.0") and self.insurance_limit == Decimal("0.0"):
            self.insurance_limit = self.insurance_included

        if not self.country_pricing:
            self.country_pricing = {"CZ": self.price}
        elif "CZ" not in self.country_pricing:
            self.country_pricing["CZ"] = self.price

        return self


class CarrierConfig(BaseModel):
    carrier: CarrierName | str = Field(..., description="Carrier identifier")
    display_name: str = Field(..., description="Human-readable carrier name")
    status: str = Field(default="ACTIVE", description="Carrier status: ACTIVE | BLOCKED-EXTERNAL")
    blocked_reason: str | None = Field(default=None, description="Explanation if carrier is BLOCKED-EXTERNAL")
    services: list[ShippingService] = Field(default_factory=list, description="List of offered services")


_PRICE_RE = re.compile(r"(\d{1,3}(?: \d{3})*(?:,\d{1,2}|,-)?) ?K\u010d")
_WS_RE = re.compile(r"\s+")


def _pdf_line_variants(raw_line: str) -> tuple[str, str]:
    """Return the (normalized, stripped) forms of a raw PDF text line.

    normalized: NFKC-stable diacritics (U+00A0 -> space, U+02C7 -> U+030C then NFC)
    with whitespace collapsed to single spaces — keeps thousand separators so the
    price pattern can read them. stripped: every whitespace char removed — used for
    space-insensitive anchor matching, since pdfplumber may merge or drop spaces
    (e.g. "PPL PARCELFOR YOUCZ", "S(30x30x30cm)").
    """
    s = raw_line.replace("\u00a0", " ").replace("\u02c7", "\u030c")
    s = unicodedata.normalize("NFC", s)
    normalized = _WS_RE.sub(" ", s).strip()
    stripped = _WS_RE.sub("", s)
    return normalized, stripped


def _pdf_price_token(token: str) -> Decimal:
    """Convert a matched price token ("1 234", "123,45", "106,-") to a Decimal."""
    n = token.replace(" ", "")
    if n.endswith(",-"):
        n = n[:-2]
    elif "," in n:
        a, _, b = n.partition(",")
        n = f"{a}.{b}"
    return Decimal(n)


def extract_price_from_pdf(pdf_path: Path, page: int, section: str, row: str, column: int) -> Decimal:
    """Extract the price at (section, row, column) from 1-indexed `page` of `pdf_path`.

    Section and row are matched against each line with whitespace removed from both
    sides (robust to pdfplumber's space handling). The price itself is read from the
    whitespace-normalized row line so thousand separators remain.

    Raises:
        ShippingValidationError: if `section` is not contained in exactly one line
            of the page, if `row` is not found in any line after the section line,
            or if `column` is not within the prices found on the row line.
    """
    if not pdf_path.exists():
        raise ShippingValidationError(f"Source PDF not found: {pdf_path}")

    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        if page < 1 or page > len(pdf.pages):
            raise ShippingValidationError(f"page {page} out of range in '{pdf_path.name}'")
        text = pdf.pages[page - 1].extract_text() or ""

    lines: list[tuple[str, str]] = []
    for raw in text.splitlines():
        normalized, stripped = _pdf_line_variants(raw)
        if normalized:
            lines.append((normalized, stripped))

    sec = _WS_RE.sub("", section)
    row_key = _WS_RE.sub("", row)

    sec_positions = [i for i, (_, st) in enumerate(lines) if sec in st]
    if len(sec_positions) != 1:
        raise ShippingValidationError(
            f"pdf_section {section!r} must occur exactly once on page {page} of '{pdf_path.name}' "
            f"(found in {len(sec_positions)} lines)"
        )
    sec_pos = sec_positions[0]

    row_pos = None
    for j in range(sec_pos + 1, len(lines)):
        if row_key in lines[j][1]:
            row_pos = j
            break
    if row_pos is None:
        raise ShippingValidationError(
            f"pdf_row {row!r} not found after pdf_section {section!r} on page {page} of '{pdf_path.name}'"
        )

    prices = [_pdf_price_token(m.group(1)) for m in _PRICE_RE.finditer(lines[row_pos][0])]
    if column >= len(prices):
        raise ShippingValidationError(
            f"pdf_column {column} out of range for pdf_row {row!r} on page {page} of '{pdf_path.name}' "
            f"(row has {len(prices)} prices: {prices})"
        )
    return prices[column]


def verify_price_cell(
    pdf_path: Path,
    page: int,
    expected: Decimal,
    anchor: PdfPriceAnchor,
    context: str,
) -> None:
    """Verify the source-PDF price at `anchor` equals `expected`; raise on mismatch."""
    got = extract_price_from_pdf(pdf_path, page, anchor.pdf_section, anchor.pdf_row, anchor.pdf_column)
    if got != expected:
        raise ShippingValidationError(
            f"{context}: price {expected} does not match source PDF '{pdf_path.name}' page {page} "
            f"(section={anchor.pdf_section!r} row={anchor.pdf_row!r} column={anchor.pdf_column} "
            f"-> PDF has {got})"
        )


class BrokerConfig(BaseModel):
    broker: str = Field(..., description="Broker identifier (e.g. delivera)")
    display_name: str = Field(..., description="Human-readable broker name")
    status: str = Field(default="ACTIVE", description="Broker status: ACTIVE | BLOCKED-EXTERNAL")
    blocked_reason: str | None = Field(default=None, description="Explanation if broker is BLOCKED-EXTERNAL")
    sender_on_documents: str = Field(default="BROKER", description="Party listed as sender on documents")
    cod_payout_destination: str = Field(default="BROKER_ACCOUNT", description="Destination for COD payment")
    claims_handler: str = Field(default="BROKER", description="Party handling reclamations")
    services: list[ShippingService] = Field(default_factory=list, description="Services offered via broker")


def _validate_services_data(path: Path, services: list[Any], root: Path) -> None:
    """Helper to validate source documents, dates, and PDF prices for services."""
    for idx, s in enumerate(services):
        if not isinstance(s, dict):
            raise ShippingValidationError(f"File '{path.name}': service #{idx} is not a valid mapping")
        s_name = s.get("name", f"service #{idx}")
        for req_field in ["source_url", "fetched_at", "source_file", "source_page"]:
            if req_field not in s or not s.get(req_field):
                raise ShippingValidationError(
                    f"File '{path.name}', service '{s_name}': missing mandatory field '{req_field}'"
                )

        source_file = s.get("source_file")
        if not isinstance(source_file, str):
            raise ShippingValidationError(
                f"File '{path.name}', service '{s_name}': source_file must be a string"
            )
        src_path = Path(source_file)
        if not src_path.is_absolute():
            cand1 = root / source_file
            cand2 = path.parent / source_file
            src_path = cand1 if cand1.exists() else cand2

        if not src_path.exists():
            raise ShippingValidationError(
                f"File '{path.name}', service '{s_name}': source_file '{source_file}' does not exist on disk"
            )

        source_page = s.get("source_page")
        if not source_page or not isinstance(source_page, int) or source_page < 1:
            raise ShippingValidationError(
                f"File '{path.name}', service '{s_name}': missing or invalid 'source_page' (must be integer >= 1)"
            )

        price_val = Decimal(str(s.get("price", 0)))
        price_anchor_raw = s.get("price_pdf")
        if not isinstance(price_anchor_raw, dict) or not price_anchor_raw:
            raise ShippingValidationError(
                f"File '{path.name}', service '{s_name}': missing mandatory 'price_pdf' anchor "
                f"(pdf_section / pdf_row / pdf_column) for the base price"
            )
        verify_price_cell(
            src_path,
            source_page,
            price_val,
            PdfPriceAnchor(**price_anchor_raw),
            context=f"File '{path.name}', service '{s_name}': base price",
        )

        country_pricing = s.get("country_pricing")
        if isinstance(country_pricing, dict) and country_pricing:
            country_anchors_raw = s.get("country_pricing_pdf")
            if not isinstance(country_anchors_raw, dict):
                raise ShippingValidationError(
                    f"File '{path.name}', service '{s_name}': missing mandatory 'country_pricing_pdf' "
                    f"anchors for country_pricing entries"
                )
            for c_code, c_price_raw in country_pricing.items():
                c_anchor_raw = country_anchors_raw.get(c_code)
                if not isinstance(c_anchor_raw, dict) or not c_anchor_raw:
                    raise ShippingValidationError(
                        f"File '{path.name}', service '{s_name}': missing 'country_pricing_pdf' anchor "
                        f"for country '{c_code}'"
                    )
                c_price = Decimal(str(c_price_raw))
                verify_price_cell(
                    src_path,
                    source_page,
                    c_price,
                    PdfPriceAnchor(**c_anchor_raw),
                    context=f"File '{path.name}', service '{s_name}': country '{c_code}' price",
                )


def load_and_validate_carrier_file(file_path: Path | str) -> CarrierConfig:
    """Load and strictly validate a carrier configuration YAML file.

    Raises:
        ShippingValidationError: naming the file, service name, and missing/invalid field.
    """
    path = Path(file_path)
    if not path.exists():
        raise ShippingValidationError(f"Carrier config file not found: {path.name}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ShippingValidationError(f"Failed to parse carrier config {path.name}: {exc}") from exc

    status = data.get("status", "ACTIVE")
    if status == "BLOCKED-EXTERNAL":
        blocked_reason = data.get("blocked_reason")
        if not blocked_reason or not str(blocked_reason).strip():
            raise ShippingValidationError(
                f"File '{path.name}': carrier with status 'BLOCKED-EXTERNAL' must provide a non-empty 'blocked_reason'"
            )
        try:
            return CarrierConfig(**data)
        except Exception as exc:
            raise ShippingValidationError(f"Validation error in file '{path.name}': {exc}") from exc

    services = data.get("services", [])
    if not isinstance(services, list):
        raise ShippingValidationError(f"File '{path.name}': 'services' must be a list")

    root = Path(__file__).resolve().parent.parent.parent.parent
    _validate_services_data(path, services, root)

    try:
        return CarrierConfig(**data)
    except Exception as exc:
        raise ShippingValidationError(f"Validation error in file '{path.name}': {exc}") from exc


def load_and_validate_broker_file(file_path: Path | str) -> BrokerConfig:
    """Load and strictly validate a broker configuration YAML file (e.g. delivera.cz).

    Raises:
        ShippingValidationError: naming the file, service name, and missing/invalid field.
    """
    path = Path(file_path)
    if not path.exists():
        raise ShippingValidationError(f"Broker config file not found: {path.name}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ShippingValidationError(f"Failed to parse broker config {path.name}: {exc}") from exc

    status = data.get("status", "ACTIVE")
    if status == "BLOCKED-EXTERNAL":
        blocked_reason = data.get("blocked_reason")
        if not blocked_reason or not str(blocked_reason).strip():
            raise ShippingValidationError(
                f"File '{path.name}': broker with status 'BLOCKED-EXTERNAL' must provide a non-empty 'blocked_reason'"
            )
        try:
            return BrokerConfig(**data)
        except Exception as exc:
            raise ShippingValidationError(f"Validation error in file '{path.name}': {exc}") from exc

    services = data.get("services", [])
    if not isinstance(services, list):
        raise ShippingValidationError(f"File '{path.name}': 'services' must be a list")

    root = Path(__file__).resolve().parent.parent.parent.parent
    _validate_services_data(path, services, root)

    try:
        return BrokerConfig(**data)
    except Exception as exc:
        raise ShippingValidationError(f"Validation error in file '{path.name}': {exc}") from exc

