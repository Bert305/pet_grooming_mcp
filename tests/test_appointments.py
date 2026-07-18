"""Tests for the appointment analytics tools."""

from datetime import datetime, timezone
from decimal import Decimal

from pet_grooming_mcp.tools import appointments


async def test_appointment_statistics_shape(fake_db):
    (
        fake_db.when(
            "avg_length_minutes",
            [
                {
                    "total_appointments": 420,
                    "completed_appointments": 351,
                    "cancelled_appointments": 38,
                    "avg_length_minutes": Decimal("62.5"),
                }
            ],
        ).when(
            "GROUP BY a.status",
            [
                {"status": "completed", "count": 351},
                {"status": "scheduled", "count": 31},
                {"status": "cancelled", "count": 38},
            ],
        )
    )

    result = await appointments.get_appointment_statistics(
        fake_db, start_date="2026-01-01", status="completed"
    )

    assert result["total_appointments"] == 420
    assert result["avg_length_minutes"] == 62.5
    assert result["by_status"][0] == {"status": "completed", "count": 351}
    assert result["filters"]["status"] == "completed"
    assert result["filters"]["start_date"] == "2026-01-01"


async def test_appointments_by_status_returns_list(fake_db):
    fake_db.when(
        "GROUP BY a.status",
        [
            {"status": "completed", "count": 351},
            {"status": "scheduled", "count": 31},
            {"status": "cancelled", "count": 38},
        ],
    )

    result = await appointments.get_appointments_by_status(fake_db)

    assert isinstance(result, list)
    assert result == [
        {"status": "completed", "count": 351},
        {"status": "scheduled", "count": 31},
        {"status": "cancelled", "count": 38},
    ]


async def test_upcoming_appointments_serialize_datetimes_and_clamp(fake_db):
    fake_db.when(
        "make_interval(days => %(days)s)",
        [
            {
                "id": 1,
                "scheduled_start": datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
                "scheduled_end": datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
                "status": "scheduled",
                "special_instructions": "Nervous around clippers",
                "pet_name": "Bella",
                "species": "dog",
                "owner_name": "Sam Johnson",
                "owner_phone": "555-0100",
                "services": "Full Groom, Nail Trim",
            }
        ],
    )

    # days_ahead below the minimum should be clamped up to 1.
    result = await appointments.get_upcoming_appointments(fake_db, days_ahead=0, limit=5)

    assert result["days_ahead"] == 1
    assert result["count"] == 1
    appt = result["appointments"][0]
    assert appt["scheduled_start"] == "2026-07-20T09:00:00+00:00"
    assert appt["pet_name"] == "Bella"
    assert appt["services"] == "Full Groom, Nail Trim"
