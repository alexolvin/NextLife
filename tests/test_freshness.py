from datetime import date
from decimal import Decimal

import pytest

from nextlife.shipping.freshness import (
    StaleTariffError,
    build_stale_tariff_notification,
    is_tariff_stale,
    validate_service_tariff_freshness,
)
from nextlife.shipping.schema import ShippingService


def test_tariff_from_two_years_ago_is_rejected():
    """DoD: Test with a 2-year-old tariff strictly causes failure and owner notification."""
    two_years_ago = date(2024, 9, 1)
    today = date(2026, 9, 8)

    # 1. Stale check
    assert is_tariff_stale(two_years_ago, max_age_days=90, current_date=today) is True

    service = ShippingService(
        name="old_service",
        service_type="Z-BOX",
        weight_max_g=5000,
        price=Decimal("70.00"),
        currency="CZK",
        source_url="https://example.com/rates-2024",
        fetched_at=two_years_ago,
        source_file="config/shipping/_sources/zasilkovna_mezi_nami_2026-09-09.pdf",
        source_page=1,
    )

    # 2. Validation raises StaleTariffError
    with pytest.raises(StaleTariffError) as exc_info:
        validate_service_tariff_freshness(service, carrier_key="packeta", max_age_days=90, current_date=today)

    msg = str(exc_info.value)
    assert "old_service" in msg
    assert "exceeds tariff_max_age_days" in msg
    assert "Publishing is prohibited" in msg

    # 3. Notification is built
    notification = build_stale_tariff_notification(
        carrier_key="packeta",
        service_name="old_service",
        fetched_at=two_years_ago,
        max_age_days=90,
    )
    assert notification["type"] == "STALE_TARIFF_WARNING"
    assert notification["carrier_key"] == "packeta"
    assert notification["service_name"] == "old_service"
    assert "action_required" in notification


def test_fresh_tariff_passes_validation():
    """Verify fresh tariff from today passes without exception."""
    today = date(2026, 9, 8)
    service = ShippingService(
        name="fresh_service",
        service_type="Z-BOX",
        weight_max_g=5000,
        price=Decimal("79.00"),
        currency="CZK",
        source_url="https://example.com/rates-2026",
        fetched_at=today,
        source_file="config/shipping/_sources/zasilkovna_mezi_nami_2026-09-09.pdf",
        source_page=1,
    )
    # Must not raise
    validate_service_tariff_freshness(service, carrier_key="packeta", max_age_days=90, current_date=today)
