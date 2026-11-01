"""Monthly sales summary service (TZ v2.0 Section III.8 / Task T-069).

DoD:
- Calculates sold count, revenue (gross), platform fees, delivery costs, packaging costs, net payout.
- Average days to sell per category.
- All numbers strictly derived from Settlement table (never on-the-fly from external sources).
- Verified by outgoing filter.
- Module length <= 400 lines.
"""

import calendar
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nextlife.comms.outgoing_filter import (
    check_context_for_internal_fields,
    render_outgoing_template,
)
from nextlife.core.exceptions import NextLifeError
from nextlife.obs.audit import record_audit_event
from nextlife.obs.truth_log import record_truth
from nextlife.storage.models import Deal, Item, Lot, Settlement

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "templates" / "reports" / "monthly_summary.jinja2"


class MonthlySummaryError(NextLifeError):
    """Raised when monthly summary calculation fails."""


def get_month_date_range(year: int, month: int) -> tuple[datetime, datetime]:
    """Return start and end datetime in UTC for the requested month."""
    _, last_day = calendar.monthrange(year, month)
    start_dt = datetime(year, month, 1, 0, 0, 0, tzinfo=UTC)
    end_dt = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=UTC)
    return start_dt, end_dt


def calculate_monthly_summary(
    session: Session,
    year: int,
    month: int,
    currency: str = "CZK",
) -> dict[str, Any]:
    """Calculate aggregate financial and category metrics strictly from Settlement records."""
    start_dt, end_dt = get_month_date_range(year, month)

    # Query settlements where settled_at or created_at falls in month
    settlements = (
        session.query(Settlement)
        .filter(
            Settlement.created_at >= start_dt,
            Settlement.created_at <= end_dt,
        )
        .all()
    )

    total_gross = Decimal("0.00")
    total_platform_fees = Decimal("0.00")
    total_delivery_costs = Decimal("0.00")
    total_packaging_costs = Decimal("0.00")
    total_net_payout = Decimal("0.00")

    category_buckets: dict[str, dict[str, Any]] = {}

    for s in settlements:
        total_gross += s.gross_amount or Decimal("0.00")
        total_platform_fees += s.platform_fee or Decimal("0.00")
        total_delivery_costs += s.delivery_cost or Decimal("0.00")
        total_packaging_costs += s.packaging_cost or Decimal("0.00")
        total_net_payout += s.net_payout or Decimal("0.00")

        deal = session.get(Deal, s.deal_id) if s.deal_id else None
        cat_key = "general"
        days_to_sell = 1.0

        if deal and deal.lot_id:
            lot = session.get(Lot, deal.lot_id)
            if lot and lot.item_id:
                item = session.get(Item, lot.item_id)
                if item and item.category_key:
                    cat_key = item.category_key
                # Time to sell from item creation to deal closure or settlement
                created_at = (item.created_at if item and item.created_at else None) or lot.created_at
                settled_at = s.settled_at or s.created_at
                if created_at and settled_at and settled_at >= created_at:
                    diff_seconds = (settled_at - created_at).total_seconds()
                    days_to_sell = max(round(diff_seconds / 86400.0, 1), 0.1)

        if cat_key not in category_buckets:
            category_buckets[cat_key] = {
                "category_key": cat_key,
                "category_name": cat_key.capitalize(),
                "sold_count": 0,
                "gross_amount": Decimal("0.00"),
                "net_payout": Decimal("0.00"),
                "days_list": [],
            }

        b = category_buckets[cat_key]
        b["sold_count"] += 1
        b["gross_amount"] += s.gross_amount or Decimal("0.00")
        b["net_payout"] += s.net_payout or Decimal("0.00")
        b["days_list"].append(days_to_sell)

    by_category = []
    for _cat_key, b in category_buckets.items():
        avg_days = round(sum(b["days_list"]) / len(b["days_list"]), 1) if b["days_list"] else 0.0
        by_category.append({
            "category_key": b["category_key"],
            "category_name": b["category_name"],
            "sold_count": b["sold_count"],
            "gross_amount": str(b["gross_amount"]),
            "net_payout": str(b["net_payout"]),
            "avg_days_to_sell": avg_days,
        })

    month_name = calendar.month_name[month]
    period_label = f"{month_name} {year} ({month:02d}.{year})"

    return {
        "year": year,
        "month": month,
        "period_label": period_label,
        "currency": currency,
        "total_sold_count": len(settlements),
        "total_gross": str(total_gross),
        "total_platform_fees": str(total_platform_fees),
        "total_delivery_costs": str(total_delivery_costs),
        "total_packaging_costs": str(total_packaging_costs),
        "total_net_payout": str(total_net_payout),
        "by_category": by_category,
    }


def render_monthly_summary(summary_data: dict[str, Any]) -> str:
    """Render human-readable text report from calculated summary data."""
    check_context_for_internal_fields(summary_data)

    if not TEMPLATE_PATH.exists():
        raise MonthlySummaryError(f"Summary template missing: {TEMPLATE_PATH}")

    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")
    return render_outgoing_template(template_str, summary_data)


def generate_and_record_monthly_summary(
    session: Session,
    year: int,
    month: int,
    currency: str = "CZK",
    actor: str = "SYSTEM",
) -> tuple[dict[str, Any], str]:
    """Execute monthly summary calculation, render text report, record audit and truth logs."""
    summary_data = calculate_monthly_summary(
        session=session,
        year=year,
        month=month,
        currency=currency,
    )
    rendered_text = render_monthly_summary(summary_data)

    record_audit_event(
        session=session,
        actor=actor,
        action="MONTHLY_SUMMARY_GENERATED",
        entity="settlement",
        entity_id=f"{year}-{month:02d}",
        data={
            "period": summary_data["period_label"],
            "total_sold_count": summary_data["total_sold_count"],
            "total_gross": summary_data["total_gross"],
            "total_net_payout": summary_data["total_net_payout"],
        },
    )

    record_truth(
        session=session,
        claim="MONTHLY_SUMMARY_PRODUCED",
        recipient="OWNER",
        evidence={
            "period": summary_data["period_label"],
            "total_sold_count": summary_data["total_sold_count"],
            "total_gross": summary_data["total_gross"],
            "total_net_payout": summary_data["total_net_payout"],
            "source": "settlement_table_only",
        },
    )
    session.flush()

    return summary_data, rendered_text
