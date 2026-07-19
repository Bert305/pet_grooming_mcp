"""Service catalogue analytics: popularity and revenue attribution."""

from __future__ import annotations

from typing import Any

from ..config import SUCCESSFUL_PAYMENT_STATUSES
from ..database import Database
from ..models import jsonable


async def get_service_statistics(db: Database) -> dict[str, Any]:
    """Return the service catalogue with pricing and lifetime booking counts."""
    totals = await db.fetchrow(
        """
        SELECT
            count(*)                          AS total_services,
            count(*) FILTER (WHERE is_active) AS active_services
        FROM services
        """
    )

    by_species = await db.fetch(
        """
        SELECT species::text AS species, count(*) AS count
        FROM services
        GROUP BY species
        ORDER BY count DESC
        """
    )

    services = await db.fetch(
        """
        SELECT
            s.id,
            s.name,
            s.species::text AS species,
            s.base_duration_minutes,
            s.base_price,
            s.is_active,
            count(aps.id) AS times_booked
        FROM services s
        LEFT JOIN appointment_services aps ON aps.service_id = s.id
        GROUP BY s.id
        ORDER BY times_booked DESC, s.name
        """
    )

    return jsonable(
        {
            "total_services": (totals or {}).get("total_services", 0),
            "active_services": (totals or {}).get("active_services", 0),
            "by_species": by_species,
            "services": services,
        }
    )


async def get_popular_services(
    db: Database, days: int = 90, limit: int = 10
) -> dict[str, Any]:
    """Rank services by number of bookings over the last ``days`` days."""
    limit = db.clamp_limit(limit, default=10)
    days = max(1, min(int(days), 3650))
    rows = await db.fetch(
        """
        SELECT
            s.id,
            s.name,
            s.species::text AS species,
            count(aps.id) AS bookings
        FROM appointment_services aps
        JOIN appointments a ON a.id = aps.appointment_id
        JOIN services s ON s.id = aps.service_id
        WHERE a.scheduled_start >= now() - make_interval(days => %(days)s)
        GROUP BY s.id
        ORDER BY bookings DESC, s.name
        LIMIT %(limit)s
        """,
        {"days": days, "limit": limit},
    )
    return jsonable({"window_days": days, "count": len(rows), "services": rows})


async def get_service_revenue(
    db: Database,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Attribute realised revenue to each service over an optional date range.

    Revenue per booked service uses the line-item ``price_override`` when set,
    otherwise the service's ``base_price``. Only bookings tied to a successfully
    paid appointment are counted.
    """
    rows = await db.fetch(
        """
        SELECT
            s.id,
            s.name,
            s.species::text AS species,
            count(aps.id) AS bookings,
            coalesce(sum(coalesce(aps.price_override, s.base_price)), 0) AS revenue
        FROM appointment_services aps
        JOIN services s ON s.id = aps.service_id
        JOIN appointments a ON a.id = aps.appointment_id
        WHERE EXISTS (
            SELECT 1 FROM payments pay
            WHERE pay.appointment_id = a.id
              AND lower(pay.status::text) = ANY(%(successful)s)
        )
          AND (%(start)s::timestamptz IS NULL OR a.scheduled_start >= %(start)s::timestamptz)
          AND (%(end)s::timestamptz IS NULL OR a.scheduled_start < %(end)s::timestamptz)
        GROUP BY s.id
        ORDER BY revenue DESC, s.name
        """,
        {
            "successful": list(SUCCESSFUL_PAYMENT_STATUSES),
            "start": start_date,
            "end": end_date,
        },
    )
    total = sum(r["revenue"] for r in rows)
    return jsonable(
        {
            "start_date": start_date,
            "end_date": end_date,
            "total_revenue": total,
            "services": rows,
        }
    )
