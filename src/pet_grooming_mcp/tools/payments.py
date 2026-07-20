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
    # Shared params + FROM/WHERE reused by all three queries below so they cover
    # the identical set of payments (bounded by the optional half-open date range
    # on paid_at).
    params = {
        "start": start_date,
        "end": end_date,
        "successful": list(SUCCESSFUL_PAYMENT_STATUSES),
    }
    where = """
        FROM payments p
        WHERE (%(start)s::timestamptz IS NULL OR p.paid_at >= %(start)s::timestamptz)
          AND (%(end)s::timestamptz IS NULL OR p.paid_at < %(end)s::timestamptz)
    """

    # Overall totals. FILTER restricts revenue and the average to successful
    # statuses only, while total_payments counts every payment in range.
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

    # Count and amount grouped by payment status (paid/refunded/failed/...).
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

    # Same shape, but grouped by payment method (card/cash/...).
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

    # Assemble the summary row with both breakdowns and echo the applied filters.
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
    # Validate group_by against the allow-list and translate it to a date_trunc
    # unit. This is what makes it safe to pass `unit` as a bound parameter — an
    # unrecognised value is rejected up front rather than reaching the query.
    unit = _TRUNC_UNITS.get(group_by.lower().strip())
    if unit is None:
        return {
            "error": f"Invalid group_by '{group_by}'. "
            f"Use one of: {', '.join(_TRUNC_UNITS)}."
        }

    # Bucket successful, actually-paid payments by the chosen period and sum
    # revenue per bucket. date_trunc appears in both SELECT and GROUP BY so each
    # row is one period; ORDER BY period yields a chronological series. The
    # paid_at IS NOT NULL guard excludes recorded-but-unpaid rows.
    rows = await db.fetch(
        """
        SELECT
            date_trunc(%(unit)s, p.paid_at) AS period,
            count(*) AS payments,
            coalesce(sum(p.amount), 0) AS revenue
        FROM payments p
        WHERE lower(p.status::text) = ANY(%(successful)s)
          AND p.paid_at IS NOT NULL
          AND (%(start)s::timestamptz IS NULL OR p.paid_at >= %(start)s::timestamptz)
          AND (%(end)s::timestamptz IS NULL OR p.paid_at < %(end)s::timestamptz)
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
    # Grand total across all buckets, returned next to the series.
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
