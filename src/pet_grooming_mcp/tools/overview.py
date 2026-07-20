"""Business-wide overview and high-level statistics."""

from __future__ import annotations

from typing import Any

from ..config import CANCELLED_STATUSES, SUCCESSFUL_PAYMENT_STATUSES
from ..database import Database
from ..models import jsonable


async def get_business_overview(db: Database) -> dict[str, Any]:
    """Return headline counts and total realised revenue for the business."""
    # Each headline figure is an independent scalar subquery in the SELECT list, so
    # the whole dashboard comes back as a single row from one round-trip. Status
    # comparisons are lower()'d for case-insensitivity, and cancelled/successful use
    # the shared status lists; revenue is coalesced to 0 if there are no payments.
    row = await db.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM users)                                  AS total_users,
            (SELECT count(*) FROM users WHERE is_active)                  AS active_users,
            (SELECT count(*) FROM pets)                                   AS total_pets,
            (SELECT count(*) FROM pets WHERE is_active)                   AS active_pets,
            (SELECT count(*) FROM appointments)                          AS total_appointments,
            (SELECT count(*) FROM appointments
              WHERE lower(status::text) = 'scheduled')                   AS scheduled_appointments,
            (SELECT count(*) FROM appointments
              WHERE lower(status::text) = 'completed')                   AS completed_appointments,
            (SELECT count(*) FROM appointments
              WHERE lower(status::text) = ANY(%(cancelled)s))            AS cancelled_appointments,
            (SELECT count(*) FROM services WHERE is_active)              AS active_services,
            (SELECT coalesce(sum(amount), 0) FROM payments
              WHERE lower(status::text) = ANY(%(successful)s))           AS total_revenue
        """,
        {
            "cancelled": list(CANCELLED_STATUSES),
            "successful": list(SUCCESSFUL_PAYMENT_STATUSES),
        },
    )
    return jsonable(row or {})


async def get_user_statistics(
    db: Database,
    created_after: str | None = None,
    created_before: str | None = None,
) -> dict[str, Any]:
    """Return customer counts and the average number of pets per customer.

    ``created_after`` / ``created_before`` are optional ISO dates (YYYY-MM-DD)
    that bound the "users created in range" figure.
    """
    # Like get_business_overview, each metric is its own scalar subquery in one
    # row. users_created_in_range applies the optional half-open date bounds.
    # avg_pets_per_user first counts pets per user in a derived table (LEFT JOIN so
    # users with zero pets count as 0), then averages those counts.
    row = await db.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM users)                       AS total_users,
            (SELECT count(*) FROM users WHERE is_active)       AS active_users,
            (SELECT count(*) FROM users WHERE NOT is_active)   AS inactive_users,
            (SELECT count(*) FROM users
              WHERE (%(after)s::timestamptz IS NULL OR created_at >= %(after)s::timestamptz)
                AND (%(before)s::timestamptz IS NULL OR created_at < %(before)s::timestamptz)
            )                                                  AS users_created_in_range,
            (SELECT round(coalesce(avg(pet_count), 0), 2) FROM (
                SELECT count(p.id) AS pet_count
                FROM users u
                LEFT JOIN pets p ON p.user_id = u.id
                GROUP BY u.id
            ) t)                                               AS avg_pets_per_user
        """,
        {"after": created_after, "before": created_before},
    )
    result = dict(row or {})
    result["created_after"] = created_after
    result["created_before"] = created_before
    return jsonable(result)


async def get_pet_statistics(db: Database) -> dict[str, Any]:
    """Return pet counts broken down by species, breed, and size category."""
    # Headline totals: all pets and the active subset.
    totals = await db.fetchrow(
        """
        SELECT
            count(*)                            AS total_pets,
            count(*) FILTER (WHERE is_active)   AS active_pets
        FROM pets
        """
    )

    # Distribution across species.
    by_species = await db.fetch(
        """
        SELECT species::text AS species, count(*) AS count
        FROM pets
        GROUP BY species
        ORDER BY count DESC
        """
    )

    # Top 25 breeds. INNER JOIN means pets with no breed assigned are excluded here
    # (they still appear under "unknown" in the size breakdown below).
    by_breed = await db.fetch(
        """
        SELECT b.name AS breed, b.species::text AS species, count(p.id) AS count
        FROM pets p
        JOIN breeds b ON b.id = p.breed_id
        GROUP BY b.name, b.species
        ORDER BY count DESC
        LIMIT 25
        """
    )

    # Distribution by breed size. LEFT JOIN keeps breed-less pets, and coalesce
    # buckets those (plus any breed with no size set) under 'unknown'.
    by_size = await db.fetch(
        """
        SELECT coalesce(b.size_category, 'unknown') AS size_category, count(p.id) AS count
        FROM pets p
        LEFT JOIN breeds b ON b.id = p.breed_id
        GROUP BY b.size_category
        ORDER BY count DESC
        """
    )

    return jsonable(
        {
            "total_pets": (totals or {}).get("total_pets", 0),
            "active_pets": (totals or {}).get("active_pets", 0),
            "by_species": by_species,
            "by_breed": by_breed,
            "by_size_category": by_size,
        }
    )
