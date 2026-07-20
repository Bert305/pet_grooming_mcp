"""Pet search, lookup, and appointment-history tools."""

from __future__ import annotations

from typing import Any

from ..database import Database
from ..models import jsonable
from .users import _like


async def search_pets(
    db: Database,
    pet_name: str | None = None,
    owner_name: str | None = None,
    species: str | None = None,
    breed: str | None = None,
    active_only: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    """Search pets by name, owner name, species, or breed.

    Text filters are case-insensitive substring matches. ``species`` matches the
    pet_species enum value (e.g. ``dog``, ``cat``). Set ``active_only=False`` to
    include inactive pets.
    """
    # Cap the caller-supplied limit to the server's configured maximum so a huge
    # value can't pull an unbounded result set.
    limit = db.clamp_limit(limit)
    # Join pets to their owner (users) and, optionally, their breed. LEFT JOIN on
    # breeds keeps pets that have no breed_id assigned. Every WHERE clause is a
    # "param IS NULL OR match" pair, so an unset filter (NULL) is a no-op and the
    # same query serves any combination of filters. ILIKE gives case-insensitive
    # substring matching; the values are wrapped with %...% by _like().
    rows = await db.fetch(
        """
        SELECT
            p.id,
            p.name AS pet_name,
            p.species::text AS species,
            b.name AS breed,
            b.size_category,
            p.date_of_birth,
            p.weight_kg,
            p.is_active,
            u.id   AS owner_id,
            u.full_name AS owner_name,
            u.phone AS owner_phone
        FROM pets p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN breeds b ON b.id = p.breed_id
        WHERE (%(pet_name)s::text IS NULL OR p.name ILIKE %(pet_name)s)
          AND (%(owner_name)s::text IS NULL OR u.full_name ILIKE %(owner_name)s)
          AND (%(species)s::text IS NULL OR lower(p.species::text) = lower(%(species)s))
          AND (%(breed)s::text IS NULL OR b.name ILIKE %(breed)s)
          AND (NOT %(active_only)s OR p.is_active)
        ORDER BY p.name
        LIMIT %(limit)s
        """,
        # Parameters are passed separately (never string-formatted) so the driver
        # handles escaping and prevents SQL injection. _like() adds the %% wildcards
        # for substring search and returns None when the argument is None.
        {
            "pet_name": _like(pet_name),
            "owner_name": _like(owner_name),
            # species is an exact enum match, so it only needs trimming — no wildcards.
            "species": species.strip() if species else None,
            "breed": _like(breed),
            "active_only": active_only,
            "limit": limit,
        },
    )
    # jsonable() converts DB types (dates, decimals, etc.) into JSON-safe values
    # before the result is handed back to the MCP client.
    return jsonable({"count": len(rows), "limit": limit, "pets": rows})


async def get_pet_appointment_history(
    db: Database, pet_id: int, limit: int = 25
) -> dict[str, Any]:
    """Return a pet's appointment history (most recent first) with services."""
    limit = db.clamp_limit(limit)

    # First look up the pet itself (one row) to confirm it exists and to grab the
    # descriptive fields returned alongside the history. fetchrow() returns a single
    # row or None.
    pet = await db.fetchrow(
        """
        SELECT p.id, p.name AS pet_name, p.species::text AS species,
               b.name AS breed, u.full_name AS owner_name
        FROM pets p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN breeds b ON b.id = p.breed_id
        WHERE p.id = %(pet_id)s
        """,
        {"pet_id": pet_id},
    )
    # Bail out early with a structured error if the id doesn't match any pet, so we
    # don't run the second query for nothing.
    if pet is None:
        return {"error": f"No pet found with id {pet_id}"}

    # Fetch the appointments for this pet. The two LEFT JOINs pull in the services
    # booked on each appointment through the appointment_services link table;
    # LEFT JOIN keeps appointments that have no services attached.
    appointments = await db.fetch(
        """
        SELECT
            a.id,
            a.scheduled_start,
            a.scheduled_end,
            a.status::text AS status,
            a.special_instructions,
            -- Collapse the multiple service rows per appointment into one
            -- comma-separated, alphabetically ordered string; coalesce() turns
            -- the NULL from an appointment with no services into an empty string.
            coalesce(string_agg(s.name, ', ' ORDER BY s.name), '') AS services
        FROM appointments a
        LEFT JOIN appointment_services aps ON aps.appointment_id = a.id
        LEFT JOIN services s ON s.id = aps.service_id
        WHERE a.pet_id = %(pet_id)s
        -- GROUP BY collapses the joined service rows so string_agg has one group
        -- per appointment; newest appointments are listed first.
        GROUP BY a.id
        ORDER BY a.scheduled_start DESC
        LIMIT %(limit)s
        """,
        {"pet_id": pet_id, "limit": limit},
    )

    # Merge the pet's descriptive fields with the appointment list (plus a count)
    # into one JSON-safe response.
    return jsonable(
        {**pet, "appointment_count": len(appointments), "appointments": appointments}
    )
