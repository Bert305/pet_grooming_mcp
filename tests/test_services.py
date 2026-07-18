"""Tests for the service analytics tools."""

from decimal import Decimal

from pet_grooming_mcp.tools import services


async def test_service_statistics_shape(fake_db):
    (
        fake_db.when("AS total_services", [{"total_services": 12, "active_services": 12}])
        .when(
            "FROM services\n        GROUP BY species",
            [{"species": "dog", "count": 8}, {"species": "cat", "count": 4}],
        )
        .when(
            "times_booked",
            [
                {
                    "id": 1,
                    "name": "Full Groom",
                    "species": "dog",
                    "base_duration_minutes": 90,
                    "base_price": Decimal("65.00"),
                    "is_active": True,
                    "times_booked": 210,
                }
            ],
        )
    )

    result = await services.get_service_statistics(fake_db)

    assert result["total_services"] == 12
    assert result["by_species"][0]["species"] == "dog"
    assert result["services"][0]["base_price"] == 65.0
    assert result["services"][0]["times_booked"] == 210


async def test_popular_services_clamps_window(fake_db):
    fake_db.when(
        "bookings",
        [{"id": 1, "name": "Nail Trim", "species": "dog", "bookings": 88}],
    )

    result = await services.get_popular_services(fake_db, days=99999, limit=5)

    # days is clamped to a 10-year maximum.
    assert result["window_days"] == 3650
    assert result["services"][0]["name"] == "Nail Trim"


async def test_service_revenue_totals(fake_db):
    fake_db.when(
        "coalesce(aps.price_override, s.base_price)",
        [
            {"id": 1, "name": "Full Groom", "species": "dog", "bookings": 100, "revenue": Decimal("6500.00")},
            {"id": 2, "name": "Nail Trim", "species": "dog", "bookings": 50, "revenue": Decimal("500.00")},
        ],
    )

    result = await services.get_service_revenue(fake_db, start_date="2026-01-01")

    assert result["total_revenue"] == 7000.0
    assert result["start_date"] == "2026-01-01"
    assert len(result["services"]) == 2
