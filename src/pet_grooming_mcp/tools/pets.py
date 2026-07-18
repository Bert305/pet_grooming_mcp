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
    limit = db.clamp_limit(limit)
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
        WHERE (%(pet_name)s IS NULL OR p.name ILIKE %(pet_name)s)
          AND (%(owner_name)s IS NULL OR u.full_name ILIKE %(owner_name)s)
          AND (%(species)s IS NULL OR lower(p.species::text) = lower(%(species)s))
          AND (%(breed)s IS NULL OR b.name ILIKE %(breed)s)
          AND (NOT %(active_only)s OR p.is_active)
        ORDER BY p.name
        LIMIT %(limit)s
        """,
        {
            "pet_name": _like(pet_name),
            "owner_name": _like(owner_name),
            "species": species.strip() if species else None,
            "breed": _like(breed),
            "active_only": active_only,
            "limit": limit,
        },
    )
    return jsonable({"count": len(rows), "limit": limit, "pets": rows})


async def get_pet_appointment_history(
    db: Database, pet_id: int, limit: int = 25
) -> dict[str, Any]:
    """Return a pet's appointment history (most recent first) with services."""
    limit = db.clamp_limit(limit)

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
    if pet is None:
        return {"error": f"No pet found with id {pet_id}"}

    appointments = await db.fetch(
        """
        SELECT
            a.id,
            a.scheduled_start,
            a.scheduled_end,
            a.status::text AS status,
            a.special_instructions,
            coalesce(string_agg(s.name, ', ' ORDER BY s.name), '') AS services
        FROM appointments a
        LEFT JOIN appointment_services aps ON aps.appointment_id = a.id
        LEFT JOIN services s ON s.id = aps.service_id
        WHERE a.pet_id = %(pet_id)s
        GROUP BY a.id
        ORDER BY a.scheduled_start DESC
        LIMIT %(limit)s
        """,
        {"pet_id": pet_id, "limit": limit},
    )

    return jsonable(
        {**pet, "appointment_count": len(appointments), "appointments": appointments}
    )
