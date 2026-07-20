"""Customer (user) search and lookup tools."""

from __future__ import annotations

from typing import Any

from ..config import SUCCESSFUL_PAYMENT_STATUSES
from ..database import Database
from ..models import jsonable


def _like(value: str | None) -> str | None:
    """Wrap a search term for a case-insensitive ILIKE, or ``None`` to skip."""
    if value is None:
        return None
    value = value.strip()
    return f"%{value}%" if value else None


async def search_users(
    db: Database,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    active_only: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    """Search customers by (partial) name, email, or phone.

    All text filters are case-insensitive substring matches. Results include a
    pet count per customer. Set ``active_only=False`` to include deactivated
    accounts.
    """
    # Clamp the caller's limit to the server maximum to bound the result size.
    limit = db.clamp_limit(limit)
    # Each WHERE line is a "param IS NULL OR match" pair, so an unset filter is a
    # no-op; ILIKE gives case-insensitive substring matching (values are wrapped
    # in %...% by _like()). The correlated subquery counts each user's pets inline.
    rows = await db.fetch(
        """
        SELECT
            u.id,
            u.full_name,
            u.email,
            u.phone,
            u.address,
            u.is_active,
            u.created_at,
            (SELECT count(*) FROM pets p WHERE p.user_id = u.id) AS pet_count
        FROM users u
        WHERE (%(name)s::text IS NULL OR u.full_name ILIKE %(name)s)
          AND (%(email)s::text IS NULL OR u.email ILIKE %(email)s)
          AND (%(phone)s::text IS NULL OR u.phone ILIKE %(phone)s)
          AND (NOT %(active_only)s OR u.is_active)
        ORDER BY u.full_name
        LIMIT %(limit)s
        """,
        # Params are bound by the driver (no string formatting) to prevent SQL
        # injection; _like() adds wildcards or returns None to disable a filter.
        {
            "name": _like(name),
            "email": _like(email),
            "phone": _like(phone),
            "active_only": active_only,
            "limit": limit,
        },
    )
    # jsonable() makes DB types (timestamps, etc.) JSON-serialisable for the client.
    return jsonable({"count": len(rows), "limit": limit, "users": rows})


async def get_user_details(db: Database, user_id: int) -> dict[str, Any]:
    """Return a full customer profile with pets, appointment count, and spend."""
    # Step 1: load the core user record and confirm the id exists.
    user = await db.fetchrow(
        """
        SELECT id, full_name, email, phone, address, preferences,
               is_active, created_at
        FROM users
        WHERE id = %(user_id)s
        """,
        {"user_id": user_id},
    )
    if user is None:
        return {"error": f"No user found with id {user_id}"}

    # Step 2: list all of this customer's pets (LEFT JOIN keeps breed-less pets).
    pets = await db.fetch(
        """
        SELECT p.id, p.name, p.species::text AS species, b.name AS breed,
               p.date_of_birth, p.weight_kg, p.is_active
        FROM pets p
        LEFT JOIN breeds b ON b.id = p.breed_id
        WHERE p.user_id = %(user_id)s
        ORDER BY p.name
        """,
        {"user_id": user_id},
    )

    # Step 3: aggregate lifetime activity across the customer's pets. Walking
    # pets -> appointments -> payments with LEFT JOINs means customers with no
    # history still return a row of zeros. count(DISTINCT a.id) avoids inflating
    # the appointment count when an appointment has several payment rows, and the
    # FILTER restricts the spend sum to successfully-paid statuses only.
    stats = await db.fetchrow(
        """
        SELECT
            count(DISTINCT a.id) AS total_appointments,
            coalesce(sum(pay.amount) FILTER (
                WHERE lower(pay.status::text) = ANY(%(successful)s)
            ), 0) AS total_spend
        FROM pets p
        LEFT JOIN appointments a ON a.pet_id = p.id
        LEFT JOIN payments pay ON pay.appointment_id = a.id
        WHERE p.user_id = %(user_id)s
        """,
        {"user_id": user_id, "successful": list(SUCCESSFUL_PAYMENT_STATUSES)},
    )

    # Merge the three queries into one profile object. `stats or {}` guards against
    # a None row so the .get() fallbacks apply.
    return jsonable(
        {
            **user,
            "pets": pets,
            "total_appointments": (stats or {}).get("total_appointments", 0),
            "total_spend": (stats or {}).get("total_spend", 0),
        }
    )


async def get_top_customers(
    db: Database, limit: int = 10, by: str = "spend"
) -> dict[str, Any]:
    """Rank customers by lifetime ``spend`` (default) or ``appointments``."""
    limit = db.clamp_limit(limit, default=10)
    # Choose the ORDER BY column from a fixed allow-list (never the raw `by` value)
    # so interpolating it into the f-string below can't cause SQL injection.
    order_column = "total_spend" if by != "appointments" else "total_appointments"
    # Same pets -> appointments -> payments roll-up as get_user_details, but for
    # every user at once: GROUP BY user, then order by the chosen metric. The
    # spend sum is filtered to successful payments and coalesced to 0.
    rows = await db.fetch(
        f"""
        SELECT
            u.id,
            u.full_name,
            u.email,
            count(DISTINCT a.id) AS total_appointments,
            coalesce(sum(pay.amount) FILTER (
                WHERE lower(pay.status::text) = ANY(%(successful)s)
            ), 0) AS total_spend
        FROM users u
        LEFT JOIN pets p ON p.user_id = u.id
        LEFT JOIN appointments a ON a.pet_id = p.id
        LEFT JOIN payments pay ON pay.appointment_id = a.id
        GROUP BY u.id, u.full_name, u.email
        ORDER BY {order_column} DESC, u.full_name
        LIMIT %(limit)s
        """,
        {"successful": list(SUCCESSFUL_PAYMENT_STATUSES), "limit": limit},
    )
    return jsonable({"ranked_by": order_column, "count": len(rows), "customers": rows})
