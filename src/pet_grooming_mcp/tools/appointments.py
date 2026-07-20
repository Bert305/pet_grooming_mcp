"""Appointment analytics and scheduling-lookahead tools."""

from __future__ import annotations

from typing import Any

from ..config import CANCELLED_STATUSES
from ..database import Database
from ..models import jsonable


async def get_appointment_statistics(
    db: Database,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    species: str | None = None,
) -> dict[str, Any]:
    """Aggregate appointment metrics, optionally filtered.

    Filters (all optional):
      * ``start_date`` / ``end_date`` — ISO dates bounding ``scheduled_start``.
      * ``status`` — appointment status (e.g. ``scheduled``, ``completed``).
      * ``species`` — pet species (e.g. ``dog``, ``cat``).

    Returns total appointments in scope, a status breakdown, completed and
    cancelled counts, and the average scheduled length in minutes.
    """
    # One shared parameter dict feeds both queries below.
    params = {
        "start": start_date,
        "end": end_date,
        "status": status.strip() if status else None,
        "species": species.strip() if species else None,
        "cancelled": list(CANCELLED_STATUSES),
    }
    # The FROM/WHERE clause is built once and reused by both the summary and the
    # by-status query so they stay in sync over the exact same filtered set. Each
    # filter is a "param IS NULL OR match" pair (an unset filter is a no-op); the
    # date bounds are half-open [start, end) so adjacent ranges don't double-count.
    where = """
        FROM appointments a
        JOIN pets p ON p.id = a.pet_id
        WHERE (%(start)s::timestamptz IS NULL OR a.scheduled_start >= %(start)s::timestamptz)
          AND (%(end)s::timestamptz IS NULL OR a.scheduled_start < %(end)s::timestamptz)
          AND (%(status)s::text IS NULL OR lower(a.status::text) = lower(%(status)s))
          AND (%(species)s::text IS NULL OR lower(p.species::text) = lower(%(species)s))
    """

    # Headline metrics in a single pass: total rows, plus conditional counts via
    # FILTER for completed/cancelled, and the average scheduled duration converted
    # from seconds (EXTRACT EPOCH of end-start) to minutes, rounded to 1 decimal.
    summary = await db.fetchrow(
        f"""
        SELECT
            count(*) AS total_appointments,
            count(*) FILTER (WHERE lower(a.status::text) = 'completed')
                AS completed_appointments,
            count(*) FILTER (WHERE lower(a.status::text) = ANY(%(cancelled)s))
                AS cancelled_appointments,
            round(avg(
                EXTRACT(EPOCH FROM (a.scheduled_end - a.scheduled_start)) / 60.0
            )::numeric, 1) AS avg_length_minutes
        {where}
        """,
        params,
    )

    # Same filtered set, broken down into a per-status count for a distribution.
    by_status = await db.fetch(
        f"""
        SELECT a.status::text AS status, count(*) AS count
        {where}
        GROUP BY a.status
        ORDER BY count DESC
        """,
        params,
    )

    # Combine the summary row with the breakdown, and echo back the filters that
    # were applied so the caller can see the scope of the numbers.
    result = dict(summary or {})
    result["by_status"] = by_status
    result["filters"] = {
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "species": species,
    }
    return jsonable(result)


async def get_appointments_by_status(
    db: Database,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Return a simple count of appointments grouped by status."""
    # Lightweight status distribution with only the optional half-open date bounds
    # (no pet join or duration maths, unlike get_appointment_statistics).
    rows = await db.fetch(
        """
        SELECT a.status::text AS status, count(*) AS count
        FROM appointments a
        WHERE (%(start)s::timestamptz IS NULL OR a.scheduled_start >= %(start)s::timestamptz)
          AND (%(end)s::timestamptz IS NULL OR a.scheduled_start < %(end)s::timestamptz)
        GROUP BY a.status
        ORDER BY count DESC
        """,
        {"start": start_date, "end": end_date},
    )
    return jsonable(rows)


async def get_upcoming_appointments(
    db: Database,
    days_ahead: int = 7,
    limit: int = 25,
) -> dict[str, Any]:
    """List upcoming (non-cancelled) appointments within the next N days.

    Each entry includes the scheduled time, pet, owner, booked services, and any
    special instructions.
    """
    limit = db.clamp_limit(limit)
    # Clamp the look-ahead window to a sane 1..365 days regardless of input.
    days_ahead = max(1, min(int(days_ahead), 365))
    # Join each appointment to its pet and owner (INNER JOINs, since every
    # appointment must have both), and LEFT JOIN through appointment_services to
    # gather the booked service names. The window is [now, now + N days); the
    # `<> ALL(cancelled)` clause drops any cancelled statuses.
    rows = await db.fetch(
        """
        SELECT
            a.id,
            a.scheduled_start,
            a.scheduled_end,
            a.status::text AS status,
            a.special_instructions,
            p.name AS pet_name,
            p.species::text AS species,
            u.full_name AS owner_name,
            u.phone AS owner_phone,
            -- One comma-separated, alphabetised service list per appointment;
            -- coalesce keeps appointments with no services as an empty string.
            coalesce(string_agg(s.name, ', ' ORDER BY s.name), '') AS services
        FROM appointments a
        JOIN pets p ON p.id = a.pet_id
        JOIN users u ON u.id = p.user_id
        LEFT JOIN appointment_services aps ON aps.appointment_id = a.id
        LEFT JOIN services s ON s.id = aps.service_id
        WHERE a.scheduled_start >= now()
          AND a.scheduled_start < now() + make_interval(days => %(days)s)
          AND lower(a.status::text) <> ALL(%(cancelled)s)
        -- Group by the appointment (plus the selected non-aggregated columns) so
        -- string_agg folds the service rows; soonest appointments come first.
        GROUP BY a.id, p.name, p.species, u.full_name, u.phone
        ORDER BY a.scheduled_start
        LIMIT %(limit)s
        """,
        {
            "days": days_ahead,
            "cancelled": list(CANCELLED_STATUSES),
            "limit": limit,
        },
    )
    return jsonable(
        {"days_ahead": days_ahead, "count": len(rows), "appointments": rows}
    )
