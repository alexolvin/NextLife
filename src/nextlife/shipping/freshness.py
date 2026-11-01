"""Tariff freshness and stale pricing validation (TZ v2.0 Section III.2.1)."""

from datetime import UTC, date, datetime
from typing import Any

from nextlife.core.exceptions import NextLifeError
from nextlife.shipping.schema import ShippingService


class StaleTariffError(NextLifeError):
    """Raised when an attempt is made to publish or quote a delivery price based on a stale tariff."""


DEFAULT_TARIFF_MAX_AGE_DAYS = 90


def is_tariff_stale(
    fetched_at: date | datetime,
    max_age_days: int = DEFAULT_TARIFF_MAX_AGE_DAYS,
    current_date: date | None = None,
) -> bool:
    """Check if tariff fetched_at exceeds maximum allowed age in days."""
    if isinstance(fetched_at, datetime):
        fetched_date = fetched_at.date()
    else:
        fetched_date = fetched_at

    ref_date = current_date or datetime.now(UTC).date()
    delta_days = (ref_date - fetched_date).days
    return delta_days > max_age_days


def validate_service_tariff_freshness(
    service: ShippingService | dict[str, Any],
    carrier_key: str = "carrier",
    max_age_days: int = DEFAULT_TARIFF_MAX_AGE_DAYS,
    current_date: date | None = None,
) -> None:
    """Validate that a service's tariff is fresh.

    Raises:
        StaleTariffError: if tariff is older than max_age_days, prohibiting publication.
    """
    if isinstance(service, dict):
        fetched_at = service.get("fetched_at")
        service_name = service.get("name", "unknown")
    else:
        fetched_at = service.fetched_at
        service_name = service.name

    if fetched_at is None:
        raise StaleTariffError(
            f"Service '{service_name}' from carrier '{carrier_key}' has no fetched_at date."
        )

    if is_tariff_stale(fetched_at, max_age_days=max_age_days, current_date=current_date):
        raise StaleTariffError(
            f"Tariff for service '{service_name}' ({carrier_key}) fetched on {fetched_at} "
            f"exceeds tariff_max_age_days ({max_age_days} days). Publishing is prohibited until owner updates tariff."
        )


def build_stale_tariff_notification(
    carrier_key: str,
    service_name: str,
    fetched_at: date | datetime,
    max_age_days: int = DEFAULT_TARIFF_MAX_AGE_DAYS,
) -> dict[str, Any]:
    """Build owner escalation notification for an outdated tariff."""
    return {
        "type": "STALE_TARIFF_WARNING",
        "carrier_key": carrier_key,
        "service_name": service_name,
        "fetched_at": str(fetched_at),
        "max_age_days": max_age_days,
        "action_required": "Please provide updated source URL and tariff in config/shipping/ before publication.",
    }
