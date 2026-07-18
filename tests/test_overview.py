"""Tests for the overview analytics tools."""

from decimal import Decimal

from pet_grooming_mcp.tools import overview


async def test_business_overview_serializes_revenue(fake_db):
    fake_db.when(
        "AS total_users",
        [
            {
                "total_users": 125,
                "active_users": 118,
                "total_pets": 163,
                "active_pets": 158,
                "total_appointments": 420,
                "scheduled_appointments": 31,
                "completed_appointments": 351,
                "cancelled_appointments": 38,
                "active_services": 12,
                "total_revenue": Decimal("28745.50"),
            }
        ],
    )

    result = await overview.get_business_overview(fake_db)

    assert result["total_users"] == 125
    assert result["completed_appointments"] == 351
    # Decimal must be normalised to a JSON-friendly float.
    assert result["total_revenue"] == 28745.50
    assert isinstance(result["total_revenue"], float)


async def test_user_statistics_echoes_date_filters(fake_db):
    fake_db.when(
        "AS total_users",
        [
            {
                "total_users": 125,
                "active_users": 118,
                "inactive_users": 7,
                "users_created_in_range": 10,
                "avg_pets_per_user": Decimal("1.30"),
            }
        ],
    )

    result = await overview.get_user_statistics(
        fake_db, created_after="2026-01-01", created_before="2026-04-01"
    )

    assert result["inactive_users"] == 7
    assert result["avg_pets_per_user"] == 1.3
    assert result["created_after"] == "2026-01-01"
    assert result["created_before"] == "2026-04-01"


async def test_pet_statistics_assembles_breakdowns(fake_db):
    (
        fake_db.when("AS active_pets", [{"total_pets": 163, "active_pets": 158}])
        .when(
            "species::text AS species, count(*)",
            [{"species": "dog", "count": 120}, {"species": "cat", "count": 43}],
        )
        .when("AS breed, b.species::text", [{"breed": "Poodle", "species": "dog", "count": 20}])
        .when("size_category", [{"size_category": "medium", "count": 60}])
    )

    result = await overview.get_pet_statistics(fake_db)

    assert result["total_pets"] == 163
    assert result["by_species"][0]["species"] == "dog"
    assert result["by_breed"][0]["breed"] == "Poodle"
    assert result["by_size_category"][0]["size_category"] == "medium"
