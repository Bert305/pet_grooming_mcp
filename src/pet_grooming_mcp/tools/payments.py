"""Payment and revenue analytics."""

from __future__ import annotations

from typing import Any

from ..config import SUCCESSFUL_PAYMENT_STATUSES
from ..database import Database
from ..models import jsonable

# Valid buckets for the revenue time-series, mapped to a safe date_trunc unit.
_TRUNC_UNITS = {"day": "day", "week": "week", "month": "month", "year": "year"}


async def get_payment_statistics(
    db: Database,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Summarise payments over an optional date range.

    Returns totals, realised revenue (successful statuses only), average
    successful payment amount, and breakdowns by status and method.
    """
    params = {
        "start": start_date,
        "end": end_date,
        "successful": list(SUCCESSFUL_PAYMENT_STATUSES),
    }
    where = """
        FROM payments p
        WHERE (%(start)s IS NULL OR p.paid_at >= %(start)s::timestamptz)
          AND (%(end)s IS NULL OR p.paid_at < %(end)s::timestamptz)
    """

    summary = await db.fetchrow(
        f"""
        SELECT
            count(*) AS total_payments,
            coalesce(sum(p.amount) FILTER (
                WHERE lower(p.status::text) = ANY(%(successful)s)
            ), 0) AS total_revenue,
            round(avg(p.amount) FILTER (
                WHERE lower(p.status::text) = ANY(%(successful)s)
            )::numeric, 2) AS avg_successful_payment
        {where}
        """,
        params,
    )

    by_status = await db.fetch(
        f"""
        SELECT p.status::text AS status, count(*) AS count,
               coalesce(sum(p.amount), 0) AS amount
        {where}
        GROUP BY p.status
        ORDER BY count DESC
        """,
        params,
    )

    by_method = await db.fetch(
        f"""
        SELECT p.method::text AS method, count(*) AS count,
               coalesce(sum(p.amount), 0) AS amount
        {where}
        GROUP BY p.method
        ORDER BY count DESC
        """,
        params,
    )

    result = dict(summary or {})
    result["by_status"] = by_status
    result["by_method"] = by_method
    result["filters"] = {"start_date": start_date, "end_date": end_date}
    return jsonable(result)


async def get_revenue_summary(
    db: Database,
    start_date: str | None = None,
    end_date: str | None = None,
    group_by: str = "month",
) -> dict[str, Any]:
    """Return a realised-revenue time series bucketed by day/week/month/year."""
    unit = _TRUNC_UNITS.get(group_by.lower().strip())
    if unit is None:
        return {
            "error": f"Invalid group_by '{group_by}'. "
            f"Use one of: {', '.join(_TRUNC_UNITS)}."
        }

    rows = await db.fetch(
        """
        SELECT
            date_trunc(%(unit)s, p.paid_at) AS period,
            count(*) AS payments,
            coalesce(sum(p.amount), 0) AS revenue
        FROM payments p
        WHERE lower(p.status::text) = ANY(%(successful)s)
          AND p.paid_at IS NOT NULL
          AND (%(start)s IS NULL OR p.paid_at >= %(start)s::timestamptz)
          AND (%(end)s IS NULL OR p.paid_at < %(end)s::timestamptz)
        GROUP BY date_trunc(%(unit)s, p.paid_at)
        ORDER BY period
        """,
        {
            "unit": unit,
            "successful": list(SUCCESSFUL_PAYMENT_STATUSES),
            "start": start_date,
            "end": end_date,
        },
    )
    total = sum(r["revenue"] for r in rows)
    return jsonable(
        {
            "group_by": unit,
            "start_date": start_date,
            "end_date": end_date,
            "total_revenue": total,
            "series": rows,
        }
    )
